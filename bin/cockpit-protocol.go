package main

import (
	"errors"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}

	var err error
	switch os.Args[1] {
	case "dispatch":
		err = cmdDispatch(os.Args[2:])
	case "send":
		err = cmdSend(os.Args[2:])
	case "tail":
		err = cmdTail(os.Args[2:])
	case "watch":
		err = cmdWatch(os.Args[2:])
	case "ask":
		err = cmdAsk(os.Args[2:])
	case "pending":
		err = cmdPending(os.Args[2:])
	case "read-question":
		err = cmdReadQuestion(os.Args[2:])
	case "reply":
		err = cmdReply(os.Args[2:])
	case "-h", "--help", "help":
		usage()
		return
	default:
		err = fmt.Errorf("unknown command: %s", os.Args[1])
	}

	if err != nil {
		fmt.Fprintf(os.Stderr, "cockpit-protocol: %v\n", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Println(`cockpit-protocol — tmux cockpit communication verbs

Usage:
  cockpit-protocol dispatch --target <session:window> (--message <text> | --message-file <path>)
  cockpit-protocol send --target <session:window> --text <text>
  cockpit-protocol tail --target <session:window> [--lines 20]
  cockpit-protocol watch --target <session:window> [--lines 20] [--interval 2]
  cockpit-protocol ask --worker <worker-name> --blocked-on <text> --question <text> [--options "A|B|C"]
  cockpit-protocol pending
  cockpit-protocol read-question --worker <worker-name>
  cockpit-protocol reply --worker <worker-name> --answer <text>`)
}

func cmdDispatch(args []string) error {
	fs := flag.NewFlagSet("dispatch", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	target := fs.String("target", "", "tmux target, e.g. session:worker-dev")
	message := fs.String("message", "", "inline mission text")
	messageFile := fs.String("message-file", "", "mission file path")
	enterDelay := fs.Int("enter-delay", 1, "seconds to wait before Enter")
	confirmDelay := fs.Int("confirm-delay", 4, "seconds to wait before status check")
	confirmLines := fs.Int("confirm-lines", 8, "lines captured for status check")
	if err := fs.Parse(args); err != nil {
		return err
	}

	if *target == "" {
		return errors.New("--target is required")
	}
	if (*message == "") == (*messageFile == "") {
		return errors.New("use exactly one of --message or --message-file")
	}

	content := *message
	if *messageFile != "" {
		b, err := os.ReadFile(*messageFile)
		if err != nil {
			return err
		}
		content = string(b)
	}
	if strings.TrimSpace(content) == "" {
		return errors.New("mission content is empty")
	}

	tmp, err := os.CreateTemp("", "cockpit-mission-*.txt")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath)
	if _, err := tmp.WriteString(content); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}

	if _, err := tmux("load-buffer", tmpPath); err != nil {
		return err
	}
	if _, err := tmux("paste-buffer", "-t", *target); err != nil {
		return err
	}
	time.Sleep(time.Duration(*enterDelay) * time.Second)
	if _, err := tmux("send-keys", "-t", *target, "", "Enter"); err != nil {
		return err
	}
	time.Sleep(time.Duration(*confirmDelay) * time.Second)
	out, err := captureTail(*target, *confirmLines)
	if err != nil {
		return err
	}
	fmt.Print(out)
	if !workerStarted(out) {
		return errors.New("worker start not confirmed (missing working status marker)")
	}
	return nil
}

func workerStarted(text string) bool {
	for _, marker := range []string{"● Working", "◉ Working", "◎ Working"} {
		if strings.Contains(text, marker) {
			return true
		}
	}
	return false
}

func cmdSend(args []string) error {
	fs := flag.NewFlagSet("send", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	target := fs.String("target", "", "tmux target, e.g. session:worker-dev")
	text := fs.String("text", "", "single-line text")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *target == "" {
		return errors.New("--target is required")
	}
	if *text == "" {
		return errors.New("--text is required")
	}
	_, err := tmux("send-keys", "-t", *target, *text, "Enter")
	return err
}

func cmdTail(args []string) error {
	fs := flag.NewFlagSet("tail", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	target := fs.String("target", "", "tmux target")
	lines := fs.Int("lines", 20, "line count")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *target == "" {
		return errors.New("--target is required")
	}
	out, err := captureTail(*target, *lines)
	if err != nil {
		return err
	}
	fmt.Print(out)
	return nil
}

func cmdWatch(args []string) error {
	fs := flag.NewFlagSet("watch", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	target := fs.String("target", "", "tmux target")
	lines := fs.Int("lines", 20, "line count")
	interval := fs.Int("interval", 2, "poll interval in seconds")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *target == "" {
		return errors.New("--target is required")
	}
	if *interval < 1 {
		return errors.New("--interval must be >= 1")
	}

	last := ""
	for {
		out, err := captureTail(*target, *lines)
		if err != nil {
			return err
		}
		if out != last {
			fmt.Printf("\n--- %s (%s) ---\n", time.Now().Format(time.RFC3339), *target)
			fmt.Print(out)
			last = out
		}
		time.Sleep(time.Duration(*interval) * time.Second)
	}
}

func cmdAsk(args []string) error {
	fs := flag.NewFlagSet("ask", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	worker := fs.String("worker", "", "worker name (worker-dev|worker-fix|worker-test)")
	blocked := fs.String("blocked-on", "", "what is blocked")
	question := fs.String("question", "", "question text")
	options := fs.String("options", "", "optional choices split by |")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *worker == "" || *blocked == "" || *question == "" {
		return errors.New("--worker, --blocked-on, and --question are required")
	}
	path := fmt.Sprintf("/tmp/%s-question.txt", *worker)
	var b strings.Builder
	b.WriteString("WORKER: " + *worker + "\n")
	b.WriteString("BLOCKED ON: " + *blocked + "\n")
	b.WriteString("QUESTION: " + *question + "\n")
	if strings.TrimSpace(*options) != "" {
		b.WriteString("OPTIONS:\n")
		for _, option := range strings.Split(*options, "|") {
			option = strings.TrimSpace(option)
			if option == "" {
				continue
			}
			b.WriteString("  - " + option + "\n")
		}
	}
	return os.WriteFile(path, []byte(b.String()), 0o600)
}

func cmdPending(args []string) error {
	fs := flag.NewFlagSet("pending", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	if err := fs.Parse(args); err != nil {
		return err
	}
	files, err := filepath.Glob("/tmp/worker-*-question.txt")
	if err != nil {
		return err
	}
	for _, f := range files {
		fmt.Println(f)
	}
	return nil
}

func cmdReadQuestion(args []string) error {
	fs := flag.NewFlagSet("read-question", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	worker := fs.String("worker", "", "worker name")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *worker == "" {
		return errors.New("--worker is required")
	}
	path := fmt.Sprintf("/tmp/%s-question.txt", *worker)
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	fmt.Print(string(b))
	return nil
}

func cmdReply(args []string) error {
	fs := flag.NewFlagSet("reply", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	worker := fs.String("worker", "", "worker name")
	answer := fs.String("answer", "", "answer text")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *worker == "" || *answer == "" {
		return errors.New("--worker and --answer are required")
	}
	path := fmt.Sprintf("/tmp/%s-answer.txt", *worker)
	return os.WriteFile(path, []byte(*answer+"\n"), 0o600)
}

func captureTail(target string, lines int) (string, error) {
	if lines < 1 {
		lines = 1
	}
	out, err := tmux("capture-pane", "-t", target, "-p")
	if err != nil {
		return "", err
	}
	return tailLines(out, lines), nil
}

func tailLines(s string, n int) string {
	lines := strings.Split(s, "\n")
	if n >= len(lines) {
		return s
	}
	return strings.Join(lines[len(lines)-n:], "\n")
}

func tmux(args ...string) (string, error) {
	cmd := exec.Command("tmux", args...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("tmux %s: %w: %s", strings.Join(args, " "), err, strings.TrimSpace(string(out)))
	}
	return string(out), nil
}

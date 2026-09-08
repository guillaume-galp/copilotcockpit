package main

import (
	"encoding/json"
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
	if !isHelpCommand(os.Args[1]) && !isDurableCommand(os.Args[1]) {
		err = validateControlStore()
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "cockpit-protocol: %v\n", err)
		os.Exit(1)
	}
	switch os.Args[1] {
	case "accept-dispatch", "record-lifecycle", "lifecycle-status", "raise-question",
		"answer-question", "cancel-mission", "replace-mission", "acknowledge-command",
		"command-status", "mission-status", "recover-dispatch":
		err = controlCommand(os.Args[1], os.Args[2:])
	case "heartbeat":
		err = controlCommand("record-lifecycle", os.Args[2:])
	case "access-prompt":
		err = controlCommand("raise-question", append(os.Args[2:], "--kind", "access-prompt"))
	case "hold":
		err = controlCommand("answer-question", append(os.Args[2:], "--hold"))
	case "meta":
		err = cmdMeta(os.Args[2:])
	case "dispatch":
		err = cmdDispatch(os.Args[2:])
	case "send":
		err = cmdSend(os.Args[2:])
	case "tail":
		err = cmdTail(os.Args[2:])
	case "watch":
		err = cmdWatch(os.Args[2:])
	case "status":
		err = cmdStatus(os.Args[2:])
	case "mission":
		err = cmdMission(os.Args[2:])
	case "nudge":
		err = cmdNudge(os.Args[2:])
	case "report":
		err = cmdReport(os.Args[2:])
	case "wait-report":
		err = cmdWaitReport(os.Args[2:])
	case "ask":
		err = controlCommand("raise-question", os.Args[2:])
	case "pending":
		err = controlCommand("mission-status", append(os.Args[2:], "--json"))
	case "read-question":
		err = controlCommand("mission-status", append(os.Args[2:], "--json"))
	case "reply":
		err = controlCommand("answer-question", os.Args[2:])
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

// Durable verbs use the existing control command contract, never pane input,
// permission inference, shared /tmp files, or a second lifecycle system.
func controlCommand(verb string, args []string) error {
	command := exec.Command(os.Getenv("COCKPIT_CONTROL_BIN"), append([]string{verb}, args...)...)
	command.Env = os.Environ()
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	return command.Run()
}

func isHelpCommand(command string) bool {
	return command == "-h" || command == "--help" || command == "help"
}

func isDurableCommand(command string) bool {
	switch command {
	case "status", "accept-dispatch", "record-lifecycle", "heartbeat", "lifecycle-status",
		"raise-question", "ask", "access-prompt", "answer-question", "reply", "hold",
		"cancel-mission", "replace-mission", "acknowledge-command", "command-status",
		"mission-status", "pending", "read-question", "recover-dispatch":
		// These verbs validate their own authority. A missing/stale derived
		// ledger must not prevent read-only diagnosis or receipt replay.
		return true
	}
	return false
}

func validateControlStore() error {
	controlBin := strings.TrimSpace(os.Getenv("COCKPIT_CONTROL_BIN"))
	if controlBin == "" {
		return errors.New("control root validation is unavailable: cockpit-protocol must run through its managed launcher")
	}
	if !filepath.IsAbs(controlBin) {
		return errors.New("control root validation is unavailable: managed cockpit-control path must be absolute")
	}

	command := exec.Command(controlBin, "validate")
	command.Env = os.Environ()
	output, err := command.CombinedOutput()
	if err == nil {
		return nil
	}
	detail := strings.TrimSpace(string(output))
	if detail == "" {
		return fmt.Errorf("control root validation failed: %w", err)
	}
	return fmt.Errorf("control root validation failed: %s", detail)
}

func usage() {
	fmt.Println(`cockpit-protocol — tmux cockpit communication verbs

Usage:
  cockpit-protocol meta current-session
  cockpit-protocol meta sessions
  cockpit-protocol meta windows [--session SESSION]
  cockpit-protocol meta resolve-target --worker <worker-name> [--session SESSION]
  cockpit-protocol meta cockpit [--session SESSION] [--json]
  cockpit-protocol dispatch --bootstrap --target <session:window> (--message <text> | --message-file <path>) [--force]
  cockpit-protocol send (--target <session:window> | --worker <worker-name> [--session SESSION]) --text <text>
  cockpit-protocol tail (--target <session:window> | --worker <worker-name> [--session SESSION]) [--lines 20]
  cockpit-protocol watch (--target <session:window> | --worker <worker-name> [--session SESSION]) [--lines 20] [--interval 2]
  cockpit-protocol status [--workers all|worker-dev,worker-test] [--session SESSION] [--json]
  cockpit-protocol mission ...  # retired; enqueue product work and run cockpit-overseer tick
  cockpit-protocol nudge --worker <worker-name> --trace-id <trace-id> --kind resend-report
  cockpit-protocol report --worker <worker-name> [--trace-id <trace-id>] [--format text|markdown]
  cockpit-protocol wait-report --worker <worker-name> [--trace-id <trace-id>] [--timeout 180]
  cockpit-protocol accept-dispatch|record-lifecycle|heartbeat <cockpit-control arguments>
  cockpit-protocol ask|raise-question|access-prompt <raise-question arguments>
  cockpit-protocol reply|answer-question|hold <answer-question arguments>
  cockpit-protocol cancel-mission|replace-mission|acknowledge-command <cockpit-control arguments>
  cockpit-protocol recover-dispatch --dispatch-command UUID --command-id UUID --worker WORKER --inspect-safe --by OPERATOR --reason REASON --evidence TYPE:REF
  cockpit-protocol pending|read-question [--worker WORKER] [--mission UUID]
  cockpit-protocol mission-status|command-status|lifecycle-status <cockpit-control arguments>

Use each durable verb's --help for its correlated IDs, digest, body references,
and acknowledgement contract. Hold never answers or approves. Cancellation and
replacement of accepted work require an applied worker acknowledgement.

Worker addressing resolves sessions in this order: explicit --session, TMUX_SESSION,
current tmux session, then auto-detection of a single cockpit session. Do not pass
--target and --worker together; cockpit-protocol rejects the ambiguity.`)
}

func cmdDispatch(args []string) error {
	fs := flag.NewFlagSet("dispatch", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	target := fs.String("target", "", "tmux target, e.g. session:worker-dev")
	worker := fs.String("worker", "", "worker name, e.g. worker-dev")
	session := fs.String("session", "", "tmux session for --worker resolution")
	message := fs.String("message", "", "inline mission text")
	messageFile := fs.String("message-file", "", "mission file path")
	bootstrap := fs.Bool("bootstrap", false, "allow setup-time worker priming only")
	force := fs.Bool("force", false, "dispatch even when worker pane looks busy")
	enterDelay := fs.Int("enter-delay", 1, "seconds to wait before Enter")
	confirmDelay := fs.Int("confirm-delay", 4, "seconds to wait before status check")
	confirmLines := fs.Int("confirm-lines", 8, "lines captured for status check")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if !*bootstrap {
		return errors.New("direct mission dispatch is retired; enqueue product work with cockpit-queue and run cockpit-overseer tick")
	}
	if strings.TrimSpace(*worker) != "" {
		return errors.New("--bootstrap requires an explicit --target and cannot address a managed worker mission")
	}

	resolvedTarget, workerAddressed, err := resolveCommandTarget(*target, *worker, *session)
	if err != nil {
		return err
	}
	if (*message == "") == (*messageFile == "") {
		return errors.New("use exactly one of --message or --message-file")
	}
	if workerAddressed && !*force {
		if err := refuseBusyWorker(resolvedTarget, *worker); err != nil {
			return err
		}
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
	if _, err := tmux("paste-buffer", "-t", resolvedTarget); err != nil {
		return err
	}
	time.Sleep(time.Duration(*enterDelay) * time.Second)
	if _, err := tmux("send-keys", "-t", resolvedTarget, "", "Enter"); err != nil {
		return err
	}
	time.Sleep(time.Duration(*confirmDelay) * time.Second)
	out, err := captureTail(resolvedTarget, *confirmLines)
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
	return hasWorkingMarker(text)
}

func cmdSend(args []string) error {
	fs := flag.NewFlagSet("send", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	target := fs.String("target", "", "tmux target, e.g. session:worker-dev")
	worker := fs.String("worker", "", "worker name, e.g. worker-dev")
	session := fs.String("session", "", "tmux session for --worker resolution")
	text := fs.String("text", "", "single-line text")
	if err := fs.Parse(args); err != nil {
		return err
	}
	resolvedTarget, _, err := resolveCommandTarget(*target, *worker, *session)
	if err != nil {
		return err
	}
	if *text == "" {
		return errors.New("--text is required")
	}
	_, err = tmux("send-keys", "-t", resolvedTarget, *text, "Enter")
	return err
}

func cmdTail(args []string) error {
	fs := flag.NewFlagSet("tail", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	target := fs.String("target", "", "tmux target")
	worker := fs.String("worker", "", "worker name, e.g. worker-dev")
	session := fs.String("session", "", "tmux session for --worker resolution")
	lines := fs.Int("lines", 20, "line count")
	if err := fs.Parse(args); err != nil {
		return err
	}
	resolvedTarget, _, err := resolveCommandTarget(*target, *worker, *session)
	if err != nil {
		return err
	}
	out, err := captureTail(resolvedTarget, *lines)
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
	worker := fs.String("worker", "", "worker name, e.g. worker-dev")
	session := fs.String("session", "", "tmux session for --worker resolution")
	lines := fs.Int("lines", 20, "line count")
	interval := fs.Int("interval", 2, "poll interval in seconds")
	if err := fs.Parse(args); err != nil {
		return err
	}
	resolvedTarget, _, err := resolveCommandTarget(*target, *worker, *session)
	if err != nil {
		return err
	}
	if *interval < 1 {
		return errors.New("--interval must be >= 1")
	}

	last := ""
	for {
		out, err := captureTail(resolvedTarget, *lines)
		if err != nil {
			return err
		}
		if out != last {
			fmt.Printf("\n--- %s (%s) ---\n", time.Now().Format(time.RFC3339), resolvedTarget)
			fmt.Print(out)
			last = out
		}
		time.Sleep(time.Duration(*interval) * time.Second)
	}
}

var defaultWorkers = []string{"worker-test", "worker-dev", "worker-fix"}

type cockpitMeta struct {
	CurrentSession string            `json:"current_session"`
	DefaultSession string            `json:"default_session"`
	Workers        map[string]string `json:"workers"`
	Windows        []string          `json:"windows"`
	Health         string            `json:"health"`
}

type workerStatus struct {
	Target          string   `json:"target"`
	Status          string   `json:"status"`
	TraceID         string   `json:"trace_id,omitempty"`
	Report          string   `json:"report,omitempty"`
	Authoritative   bool     `json:"authoritative"`
	MissionID       string   `json:"mission_id,omitempty"`
	CommandID       string   `json:"command_id,omitempty"`
	Lifecycle       string   `json:"lifecycle,omitempty"`
	PendingCommands []string `json:"pending_commands,omitempty"`
	Reachable       bool     `json:"reachable"`
	Observation     string   `json:"observation,omitempty"`
}

type statusPayload struct {
	Session string                  `json:"session"`
	Workers map[string]workerStatus `json:"workers"`
}

func cmdMeta(args []string) error {
	if len(args) < 1 {
		return errors.New("meta requires a subcommand: current-session, sessions, windows, resolve-target, cockpit")
	}
	switch args[0] {
	case "current-session":
		session, err := currentSession()
		if err != nil {
			return err
		}
		fmt.Println(session)
		return nil
	case "sessions":
		sessions, err := listSessions()
		if err != nil {
			return err
		}
		for _, session := range sessions {
			fmt.Println(session)
		}
		return nil
	case "windows":
		fs := flag.NewFlagSet("meta windows", flag.ContinueOnError)
		fs.SetOutput(os.Stderr)
		session := fs.String("session", "", "tmux session")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		resolved, err := resolveSession(*session)
		if err != nil {
			return err
		}
		windows, err := listWindows(resolved)
		if err != nil {
			return err
		}
		for _, window := range windows {
			fmt.Println(window)
		}
		return nil
	case "resolve-target":
		fs := flag.NewFlagSet("meta resolve-target", flag.ContinueOnError)
		fs.SetOutput(os.Stderr)
		worker := fs.String("worker", "", "worker name")
		session := fs.String("session", "", "tmux session")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if *worker == "" {
			return errors.New("--worker is required")
		}
		target, err := resolveWorkerTarget(*worker, *session)
		if err != nil {
			return err
		}
		fmt.Println(target)
		return nil
	case "cockpit":
		fs := flag.NewFlagSet("meta cockpit", flag.ContinueOnError)
		fs.SetOutput(os.Stderr)
		session := fs.String("session", "", "tmux session")
		asJSON := fs.Bool("json", false, "print JSON")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		meta, err := buildCockpitMeta(*session)
		if err != nil {
			return err
		}
		if *asJSON {
			return printJSON(meta)
		}
		fmt.Printf("current_session: %s\n", meta.CurrentSession)
		fmt.Printf("default_session: %s\n", meta.DefaultSession)
		fmt.Printf("health: %s\n", meta.Health)
		fmt.Println("workers:")
		for _, worker := range defaultWorkers {
			fmt.Printf("  %s: %s\n", worker, meta.Workers[worker])
		}
		fmt.Println("windows:")
		for _, window := range meta.Windows {
			fmt.Printf("  %s\n", window)
		}
		return nil
	default:
		return fmt.Errorf("unknown meta subcommand: %s", args[0])
	}
}

func cmdStatus(args []string) error {
	fs := flag.NewFlagSet("status", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	session := fs.String("session", "", "tmux session")
	workersArg := fs.String("workers", "all", "all or comma-separated worker names")
	asJSON := fs.Bool("json", false, "print JSON")
	lines := fs.Int("lines", 80, "tail lines to inspect")
	if err := fs.Parse(args); err != nil {
		return err
	}
	workers := parseWorkers(*workersArg)
	command := exec.Command(os.Getenv("COCKPIT_CONTROL_BIN"), "protocol-status", "--workers", strings.Join(workers, ","))
	output, err := command.Output()
	if err != nil {
		return fmt.Errorf("durable status unavailable: %w", err)
	}
	resolved, sessionErr := resolveSession(*session)
	payload := statusPayload{Session: resolved}
	if err := json.Unmarshal(output, &payload); err != nil {
		return err
	}
	for _, worker := range workers {
		target := fmt.Sprintf("%s:%s", resolved, worker)
		text, err := "", sessionErr
		if err == nil {
			text, err = captureTail(target, *lines)
		}
		status := payload.Workers[worker]
		status.Target = target
		status.Reachable = err == nil
		if err == nil {
			status.Observation = statusLabel(text)
			status.Report = firstLine(extractReport(text))
		} else if !status.Authoritative {
			status.Status = "unreachable"
		}
		payload.Workers[worker] = status
	}
	if *asJSON {
		return printJSON(payload)
	}
	for _, worker := range workers {
		status := payload.Workers[worker]
		fmt.Printf("%s %s %s", worker, status.Status, status.Target)
		if status.TraceID != "" {
			fmt.Printf(" trace=%s", status.TraceID)
		}
		if status.Report != "" {
			fmt.Printf(" report=%q", status.Report)
		}
		fmt.Println()
	}
	return nil
}

func cmdMission(args []string) error {
	return errors.New("mission is retired because it bypasses durable controller state; enqueue product work with cockpit-queue and run cockpit-overseer tick")
}

func cmdNudge(args []string) error {
	fs := flag.NewFlagSet("nudge", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	worker := fs.String("worker", "", "worker name")
	session := fs.String("session", "", "tmux session")
	traceID := fs.String("trace-id", "", "trace id")
	kind := fs.String("kind", "", "nudge kind")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *worker == "" || *traceID == "" || *kind == "" {
		return errors.New("--worker, --trace-id, and --kind are required")
	}
	text := fmt.Sprintf("TRACE-ID: %s | NUDGE: %s", *traceID, *kind)
	if *kind == "resend-report" {
		text = fmt.Sprintf("TRACE-ID: %s | NUDGE: please resend your latest structured report.", *traceID)
	}
	target, err := resolveWorkerTarget(*worker, *session)
	if err != nil {
		return err
	}
	_, err = tmux("send-keys", "-t", target, text, "Enter")
	return err
}

func cmdReport(args []string) error {
	fs := flag.NewFlagSet("report", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	worker := fs.String("worker", "", "worker name")
	session := fs.String("session", "", "tmux session")
	traceID := fs.String("trace-id", "", "trace id to prefer")
	format := fs.String("format", "text", "text or markdown")
	lines := fs.Int("lines", 200, "tail lines to inspect")
	if err := fs.Parse(args); err != nil {
		return err
	}
	report, err := latestWorkerReport(*worker, *session, *traceID, *lines)
	if err != nil {
		return err
	}
	if *format == "markdown" {
		fmt.Println("```")
		fmt.Print(report)
		if !strings.HasSuffix(report, "\n") {
			fmt.Println()
		}
		fmt.Println("```")
		return nil
	}
	if *format != "text" {
		return errors.New("--format must be text or markdown")
	}
	fmt.Print(report)
	if !strings.HasSuffix(report, "\n") {
		fmt.Println()
	}
	return nil
}

func cmdWaitReport(args []string) error {
	fs := flag.NewFlagSet("wait-report", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	worker := fs.String("worker", "", "worker name")
	session := fs.String("session", "", "tmux session")
	traceID := fs.String("trace-id", "", "trace id to prefer")
	timeout := fs.Int("timeout", 180, "timeout in seconds")
	lines := fs.Int("lines", 200, "tail lines to inspect")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *timeout < 1 {
		return errors.New("--timeout must be >= 1")
	}
	deadline := time.Now().Add(time.Duration(*timeout) * time.Second)
	var lastErr error
	for {
		report, err := latestWorkerReport(*worker, *session, *traceID, *lines)
		if err == nil {
			fmt.Print(report)
			if !strings.HasSuffix(report, "\n") {
				fmt.Println()
			}
			return nil
		}
		lastErr = err
		if time.Now().After(deadline) {
			return fmt.Errorf("timed out waiting for report: %v", lastErr)
		}
		time.Sleep(2 * time.Second)
	}
}

func resolveCommandTarget(target string, worker string, session string) (string, bool, error) {
	target = strings.TrimSpace(target)
	worker = strings.TrimSpace(worker)
	if target != "" && worker != "" {
		return "", false, errors.New("use --target or --worker, not both")
	}
	if target != "" {
		return target, false, nil
	}
	if worker == "" {
		return "", false, errors.New("--target or --worker is required")
	}
	resolved, err := resolveWorkerTarget(worker, session)
	return resolved, true, err
}

func resolveWorkerTarget(worker string, session string) (string, error) {
	worker = strings.TrimSpace(worker)
	if worker == "" {
		return "", errors.New("--worker is required")
	}
	resolvedSession, err := resolveSession(session)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%s:%s", resolvedSession, worker), nil
}

func resolveSession(explicit string) (string, error) {
	if strings.TrimSpace(explicit) != "" {
		return strings.TrimSpace(explicit), nil
	}
	if envSession := strings.TrimSpace(os.Getenv("TMUX_SESSION")); envSession != "" {
		return envSession, nil
	}
	if session, err := currentTmuxSession(); err == nil && strings.TrimSpace(session) != "" {
		return strings.TrimSpace(session), nil
	}
	return autodetectCockpitSession()
}

func currentSession() (string, error) {
	return resolveSession("")
}

func currentTmuxSession() (string, error) {
	return tmux("display-message", "-p", "#S")
}

func autodetectCockpitSession() (string, error) {
	sessions, err := listSessions()
	if err != nil {
		return "", fmt.Errorf("could not resolve session: set --session or TMUX_SESSION (%w)", err)
	}
	var candidates []string
	for _, session := range sessions {
		windows, err := listWindows(session)
		if err != nil {
			continue
		}
		if hasAllWorkers(windows) {
			candidates = append(candidates, session)
		}
	}
	if len(candidates) == 1 {
		return candidates[0], nil
	}
	if len(candidates) == 0 {
		return "", errors.New("could not auto-detect a cockpit session with worker-test, worker-dev, and worker-fix windows; pass --session")
	}
	return "", fmt.Errorf("multiple cockpit sessions found (%s); pass --session", strings.Join(candidates, ", "))
}

func listSessions() ([]string, error) {
	out, err := tmux("list-sessions", "-F", "#S")
	if err != nil {
		return nil, err
	}
	return nonEmptyLines(out), nil
}

func listWindows(session string) ([]string, error) {
	out, err := tmux("list-windows", "-t", session, "-F", "#W")
	if err != nil {
		return nil, err
	}
	return nonEmptyLines(out), nil
}

func buildCockpitMeta(session string) (cockpitMeta, error) {
	resolved, err := resolveSession(session)
	if err != nil {
		return cockpitMeta{}, err
	}
	windows, err := listWindows(resolved)
	if err != nil {
		return cockpitMeta{}, err
	}
	workers := map[string]string{}
	for _, worker := range defaultWorkers {
		workers[worker] = fmt.Sprintf("%s:%s", resolved, worker)
	}
	health := "ok"
	if !hasAllWorkers(windows) {
		health = "degraded"
	}
	return cockpitMeta{
		CurrentSession: resolved,
		DefaultSession: resolved,
		Workers:        workers,
		Windows:        windows,
		Health:         health,
	}, nil
}

func hasAllWorkers(windows []string) bool {
	seen := map[string]bool{}
	for _, window := range windows {
		seen[window] = true
	}
	for _, worker := range defaultWorkers {
		if !seen[worker] {
			return false
		}
	}
	return true
}

func parseWorkers(value string) []string {
	if strings.TrimSpace(value) == "" || strings.TrimSpace(value) == "all" {
		return append([]string{}, defaultWorkers...)
	}
	var workers []string
	for _, item := range strings.Split(value, ",") {
		item = strings.TrimSpace(item)
		if item != "" {
			workers = append(workers, item)
		}
	}
	if len(workers) == 0 {
		return append([]string{}, defaultWorkers...)
	}
	return workers
}

func refuseBusyWorker(target string, worker string) error {
	out, err := captureTail(target, 80)
	if err != nil {
		return err
	}
	status := statusLabel(out)
	if status != "working" {
		return nil
	}
	return fmt.Errorf("worker %s at %s looks busy (status=%s); inspect with: cockpit-protocol status --workers %s --json; retry with --force to override", worker, target, status, worker)
}

func statusLabel(text string) string {
	if hasWorkingMarker(text) {
		return "working"
	}
	upper := strings.ToUpper(text)
	if strings.Contains(upper, "BLOCKED") || strings.Contains(upper, "ESCALATE") {
		return "blocked"
	}
	lower := strings.ToLower(text)
	if strings.Contains(text, "❯") || strings.Contains(lower, "ready") {
		return "available"
	}
	return "idle"
}

func hasWorkingMarker(text string) bool {
	for _, marker := range []string{"● Working", "◉ Working", "◎ Working"} {
		if strings.Contains(text, marker) {
			return true
		}
	}
	return false
}

func latestWorkerReport(worker string, session string, traceID string, lines int) (string, error) {
	if strings.TrimSpace(worker) == "" {
		return "", errors.New("--worker is required")
	}
	target, err := resolveWorkerTarget(worker, session)
	if err != nil {
		return "", err
	}
	text, err := captureTail(target, lines)
	if err != nil {
		return "", err
	}
	report := extractReport(preferTraceWindow(text, traceID))
	if report == "" {
		return "", errors.New("no structured worker report found in pane tail")
	}
	return report, nil
}

func preferTraceWindow(text string, traceID string) string {
	traceID = strings.TrimSpace(traceID)
	if traceID == "" || !strings.Contains(text, traceID) {
		return text
	}
	idx := strings.LastIndex(text, traceID)
	start := idx
	if before := strings.LastIndex(text[:idx], "\nTRACE-ID:"); before >= 0 {
		start = before
	}
	return text[start:]
}

func extractReport(text string) string {
	lines := strings.Split(text, "\n")
	start := -1
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if strings.HasPrefix(line, "WORKER-") || strings.HasPrefix(line, "ROOT CAUSE:") {
			start = i
			break
		}
	}
	if start < 0 {
		return ""
	}
	end := len(lines)
	for i := start + 1; i < len(lines); i++ {
		line := strings.TrimSpace(lines[i])
		if line == "" {
			end = i
			break
		}
		if strings.HasPrefix(line, "❯") || strings.HasPrefix(line, "$ ") {
			end = i
			break
		}
	}
	return strings.TrimRight(strings.Join(lines[start:end], "\n"), "\n")
}

func latestTraceID(text string) string {
	for _, line := range reverseLines(text) {
		for _, prefix := range []string{"TRACE-ID:", "TRACE_ID:"} {
			if strings.Contains(line, prefix) {
				parts := strings.SplitN(line, prefix, 2)
				return strings.TrimSpace(parts[1])
			}
		}
	}
	return ""
}

func reverseLines(text string) []string {
	lines := strings.Split(text, "\n")
	for i, j := 0, len(lines)-1; i < j; i, j = i+1, j-1 {
		lines[i], lines[j] = lines[j], lines[i]
	}
	return lines
}

func nonEmptyLines(text string) []string {
	var lines []string
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			lines = append(lines, line)
		}
	}
	return lines
}

func firstLine(text string) string {
	text = strings.TrimSpace(text)
	if text == "" {
		return ""
	}
	if idx := strings.IndexByte(text, '\n'); idx >= 0 {
		return text[:idx]
	}
	return text
}

func printJSON(value any) error {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	return encoder.Encode(value)
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

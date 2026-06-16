# Portability cheat-sheet — BSD/macOS vs GNU/Linux shell tools

> **Spike:** `TH1-E6-US1` — macOS/BSD portability of bootstrap bash scripts
> **Purpose:** A verified reference of the GNU↔BSD tool divergences that affect
> `lib/common.sh` (E1) and the template runners (E3), so generated scripts run
> identically on Linux and macOS. Satisfies **NFR-2**; see architecture §11
> (Risks & spikes).

The golden rule for the whole toolkit: **assume the lowest common denominator**.
macOS ships BSD userland tools and an old bash (3.2). Prefer POSIX-only flags,
and where a flag diverges, route the operation through a temp file + `mv` or
through `python3` (present on macOS 12.3+ as `python3`, and on every Linux box
we target).

---

## 1. Hazard table

| # | Hazard | GNU form (Linux) | BSD form (macOS) | Single portable recommendation |
|---|--------|------------------|------------------|--------------------------------|
| 1 | In-place edit | `sed -i 's/a/b/' f` | `sed -i '' 's/a/b/' f` (requires explicit empty backup suffix) | **Never use `sed -i`.** Write to a temp file and `mv` it back: `sed 's/a/b/' f > "$tmp" && mv "$tmp" f`. Works identically everywhere and is atomic. |
| 2 | Date formatting / math | `date -d '2020-01-02 03:04:05' +%s`; `date -d @<epoch> +%F` | `date -r <epoch> +%F`; `date -v+1d`/`-v-1H` for arithmetic; **no `-d`** | **Use `python3` for any date parsing/math.** For a plain UTC timestamp use `date -u +%Y-%m-%dT%H:%M:%SZ` (this exact form is portable). For parsing or arithmetic: `python3 -c 'import datetime,sys; ...'`. |
| 3 | File metadata | `stat -c '%s' f` (size), `stat -c '%Y' f` (mtime epoch) | `stat -f '%z' f` (size), `stat -f '%m' f` (mtime epoch) | **Avoid `stat` for portable scripts.** For size use `wc -c < f`. For mtime epoch use `python3 -c 'import os,sys;print(int(os.path.getmtime(sys.argv[1])))' f`. If `stat` is unavoidable, branch on `uname -s`. |
| 4 | Canonical path | `readlink -f link` (resolves to absolute real path) | `readlink -f` **absent**; BSD `readlink` has no `-f`. (`greadlink`/`realpath` only if coreutils installed) | **Use a shell/Python fallback, never bare `readlink -f`.** Portable one-liner: `python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$p"`. For directories, `(cd "$dir" && pwd -P)` is pure-POSIX. |
| 5 | Temp file/dir creation | `mktemp` (bare, no template, OK); `mktemp -d`; trailing-X template `mktemp fooXXXXXX` | `mktemp` **requires** a template *or* `-t prefix`; bare `mktemp` errors on some BSD variants; `-t` semantics differ (BSD treats arg as prefix, GNU as full template under `$TMPDIR`) | **Always pass an explicit template with at least 6 trailing X and no path:** `mktemp "${TMPDIR:-/tmp}/cc.XXXXXX"` and `mktemp -d "${TMPDIR:-/tmp}/cc.XXXXXX"`. Avoid `-t`. Always `trap 'rm -rf "$tmp"' EXIT`. |
| 6 | `grep -P` (PCRE) | `grep -P '\d+'` supported | `grep -P` **not supported** by BSD grep (returns error) | **Never use `grep -P`.** Use a POSIX ERE with `grep -Eo '[0-9]+'` instead, or `sed`/`awk`. |
| 7 | `grep -o` (only-matching) | `grep -o 'pat'` supported | `grep -o` **is** supported on modern BSD grep — generally safe | `grep -o` is OK; pair it with `-E` (`grep -Eo`) and POSIX ERE classes (`[[:digit:]]`) rather than `\d`. |

**Bonus hazards worth a line (same temp-file/`python3` doctrine applies):**

- **`echo -e` / `echo -n`** — flag handling differs between shells/builtins. Use `printf` exclusively.
- **`sed -E` vs `sed -r`** — `-E` is portable (GNU accepts it as alias for `-r`; BSD only knows `-E`). Always use `-E`.
- **`bash` version** — macOS system bash is **3.2** (no associative arrays, no `${var^^}`, no `mapfile`). Target bash 3.2 features only, or shebang `#!/usr/bin/env bash` and document a `brew install bash` path.

---

## 2. Tested tool versions & OS

Versions below were captured by **running the commands in this environment**.
This box is **GNU/Linux only**, so:

- ✅ **Empirically verified here (GNU/Linux forms):** rows 1–7 GNU columns and the
  portable recommendations (`sed > tmp && mv`, `date -u +...`, `wc -c`, `python3`
  realpath/mtime, `mktemp` template, `grep -Eo`) were all executed and produced
  the expected output.
- 📖 **Documented from knowledge (BSD/macOS forms):** the BSD columns are *not*
  runnable here; they are documented from BSD/macOS man-page behaviour and must
  be re-verified on a real macOS host before E1/E3 ship.

| Tool | Version (this host) | Verified empirically |
|------|---------------------|----------------------|
| OS | `Ubuntu 26.04 LTS` — `Linux 6.6.114.1-microsoft-standard-WSL2 x86_64 GNU/Linux` | n/a |
| bash | `GNU bash, version 5.3.9(1)-release (x86_64-pc-linux-gnu)` | ✅ |
| sed | `sed (GNU sed) 4.9` | ✅ (`sed -i`, `sed > tmp && mv`) |
| date | `date (uutils coreutils) 0.8.0` (GNU-compatible flags) | ✅ (`-d`, `-u`, `@epoch`) |
| stat | `stat (uutils coreutils) 0.8.0` | ✅ (`stat -c '%s' '%Y'`) |
| grep | `grep (GNU grep) 3.12` | ✅ (`-P`, `-o`, `-Eo`) |
| readlink | `readlink (uutils coreutils) 0.8.0` | ✅ (`readlink -f`) |
| mktemp | `mktemp (uutils coreutils) 0.8.0` | ✅ (template + `-d`) |
| python3 | `Python 3.13.13` | ✅ (date + realpath fallbacks) |

> ⚠️ Note: the `date`/`stat`/`readlink`/`mktemp` here are **uutils coreutils 0.8.0**
> (a GNU-compatible Rust reimplementation), not GNU coreutils proper. The GNU
> *flag* behaviour matched expectations, but this reinforces the recommendation
> to avoid tool-specific flags entirely in favour of `python3`/temp-file forms.

Reproduce with:

```sh
uname -a; cat /etc/os-release
bash --version  | head -1
sed --version   | head -1
date --version  | head -1
stat --version  | head -1
grep --version  | head -1
readlink --version | head -1
mktemp --version   | head -1
python3 --version
```

---

## 3. One-line summary (copy-paste into E1-US1 / E3-US1 `## Notes`)

```text
Portability (TH1-E6-US1) — adopt these in lib/common.sh and all template runners:
- In-place edit: NEVER `sed -i`. Use `sed 's/../../' f > "$tmp" && mv "$tmp" f` (atomic, portable).
- Regex flags: use `-E` not `-r`; use `grep -Eo '[0-9]+'`, NEVER `grep -P` (`\d` unsupported on BSD).
- Dates: plain stamp via `date -u +%Y-%m-%dT%H:%M:%SZ`; any parse/math via `python3` (no `date -d` on macOS).
- File mtime/size: `wc -c < f` for size; `python3 ... os.path.getmtime` for mtime. Avoid `stat -c`/`stat -f`.
- Canonical path: NEVER `readlink -f`. Use `python3 ... os.path.realpath`, or `(cd "$d" && pwd -P)` for dirs.
- Temp files: `mktemp "${TMPDIR:-/tmp}/cc.XXXXXX"` (explicit template, no `-t`) + `trap 'rm -rf "$tmp"' EXIT`.
- Output: use `printf`, never `echo -e`/`echo -n`. Target bash 3.2 (macOS system bash) — no assoc arrays/mapfile.
```

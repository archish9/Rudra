# Verification

Rudra decides a task is finished by running a **deterministic gate** — not by
asking a model whether it is done, and not by checking that a file exists.

Run it yourself:

```bash
rudra verify              # the files git reports as changed
rudra verify --all        # every source file in the project
rudra verify --json       # machine-readable
```

Exit codes: `0` passed · `1` a blocking stage failed · `2` something needs you
(a denied command, a missing tool, an internal error).

## The five stages

| Stage | Blocks? | What it asks |
|---|---|---|
| syntax | yes | Does every changed file parse? |
| lint | **no — advisory** | What does the linter say? Reported in full, never fails the task |
| typecheck | yes, *except* Rudra's bundled mypy — advisory | Do the types hold? |
| test | yes | Does the project's own suite pass? |
| stubs | yes | Are there placeholders in the files this run touched? |

Stages run in that order and **stop at the first blocking failure**. A file that
does not parse makes everything downstream noise.

The stub scan is the reason this is more than "run the tests". An agent can pass
a suite and still leave `pass` where an implementation belongs.

## The six outcomes

| Outcome | Meaning |
|---|---|
| `passed` | ran, clean |
| `failed` | ran, found problems |
| `not_applicable` | this stack has no such stage — plain JavaScript has no type checker |
| `covered_by` | subsumed by a later stage — `cargo check` and `tsc --noEmit` are already the parse step |
| `missing_tool` | the stage applies here, but the tool is not installed. See the matrix below |
| `denied` | the permission gate refused the command |

A verdict never says a bare "passed". It always names the stages that produced
no judgement, so a clean-looking result cannot hide three stages that never ran.

## What counts as a placeholder

The stub scan looks at **only the files this run changed**. A pre-existing `TODO`
in code Rudra never touched is not Rudra's defect.

| Language | Detected |
|---|---|
| Python | a function whose entire body is `pass`, `...`, a docstring, or `raise NotImplementedError`; `TODO`/`FIXME` comments |
| Rust | `todo!()`, `unimplemented!()`, `TODO`/`FIXME` |
| JavaScript / TypeScript | `throw new Error("not implemented")` and variants, `TODO`/`FIXME` |

Python is analysed with the `ast` and `tokenize` modules, not a regex, so a
legitimate `pass` inside an `except:` block, a `class Foo: pass`, or a
`while True: pass` is never mistaken for a stub — and the word `TODO` inside a
string is not mistaken for a comment. The other languages use line matching,
which is shallower.

## Installing the tools

Rudra **ships Python's toolchain**. Everything else belongs to your project's
own ecosystem, which a Python package cannot vendor.

| Stack | What you need |
|---|---|
| **Python** | Nothing. `ruff` and `mypy` ship with Rudra |
| **Rust** | `cargo` (comes with rustup) and, for lint, `rustup component add clippy` |
| **TypeScript** | `typescript` in your `devDependencies`, installed into `node_modules` |
| **Any JS/TS** | `eslint` in your `devDependencies`, for the advisory lint stage |

Rudra prefers **your** tool over its own whenever you have one. A `ruff` or
`mypy` in the project's `.venv/bin/` wins over the bundled copy, because it is
the version your project pins.

### Python {#python}

Nothing to install. If your project has no `mypy` of its own, Rudra runs its
bundled copy with `--ignore-missing-imports --no-site-packages` and an empty
`MYPYPATH` — it cannot resolve your dependencies, so it checks your code rather
than your imports, and the report says so.

The last two flags are why the answer is the same on your machine and on
someone else's: without them mypy resolves whatever happens to be installed
*beside Rudra*, so a Rudra installed next to Flask checks your Flask app
against the real Flask and a clean one checks it against `Any`.

**That bundled verdict is advisory: it is reported in full and never fails a
task.** Reproducible is not the same as informed. The flags above fix *which*
answer you get; they cannot give mypy your dependencies, so it is still
checking your code against `Any` wherever a third-party type would have said
something. That is a useful report and a bad gate.

**Install `mypy` into your project's `.venv` and it blocks again.** That copy
sees your real dependencies and reads your own `mypy.ini` or `[tool.mypy]`, so
its answer is yours and it is reproducible:

```bash
python -m pip install mypy      # inside your project's .venv
```

Either way the findings are printed, and `rudra verify` still shows them under
`typecheck`, tagged `(advisory)` when they came from the bundled copy.

### Your project's `.venv` {#project-venv}

During a run, a Python project's tests run in a `.venv` that Rudra keeps. If
the project has no virtualenv of its own, Rudra creates `.venv` with your
machine's `python3` before the first task. Before every gate run it then
installs pytest plus what the project declares: every `requirements*.txt` at
the root, and `pyproject.toml`'s `[project] dependencies`,
`[project.optional-dependencies]` and `[dependency-groups]`. It reinstalls only
after one of those files changes.

Rudra leaves alone any virtualenv it did not create: a `.venv` or `venv`
already in the project, or one you activated before starting Rudra (Poetry,
Pipenv). Its own is marked with `.venv/.rudra-sync.json`. You can delete
`.venv` at any time, and the next run rebuilds it.

These installs are commands, so they follow the gate's rules: `ask` mode shows
you each one first, and `--auto` without `--allow-shell` denies them. If an
install fails, the coder sees pip's output next to the failing tests.
`rudra verify` on its own installs nothing.

Every shell command Rudra runs, the agents' included, carries
`PIP_REQUIRE_VIRTUALENV=1`. pip then refuses to install into a Python that is
not a virtualenv, so an agent cannot change the packages on your machine.

Not covered: editable installs and `setup.py`-only projects (declare the
dependencies in `requirements.txt` instead), and conda environments, which pip
does not count as virtualenvs.

### Rust {#rust}

`cargo check` covers both syntax and typecheck. Lint needs clippy:

```bash
rustup component add clippy
```

### TypeScript {#typescript}

Rudra treats a project as TypeScript when `package.json` declares `typescript`
or a `tsconfig.json` exists. It then needs the compiler installed:

```bash
npm install --save-dev typescript
```

Without it, typecheck reports `missing_tool` and stops — a project that declares
TypeScript and cannot run `tsc` is a broken setup, not a project without types.

### ESLint {#eslint}

```bash
npm install --save-dev eslint
```

Lint is advisory, so a missing eslint never fails a task. It is still reported.

### Node {#node}

Plain JavaScript is parse-checked with `node --check`, so `node` must be on
PATH. It has no type checker, and typecheck correctly reports `not_applicable`.

## Verification and `--auto`

Every stage except Python's syntax check and the stub scan runs a command, and
commands go through the permission gate as `execute`. Under `--auto` without
`--allow-shell`, they are denied — the same rule that already governs
`run_tests`.

An unattended run that should verify its own work needs:

```bash
rudra --auto --allow-shell "..."
```

In `ask` mode nothing changes: you see each command before it runs. Note that
`ask` needs a real terminal — piped or redirected stdin cannot answer a prompt,
so every command stage is rejected and the run exits 2.

## Where the output goes

The table is a summary. Every stage's full output is written to
`.rudra/run/logs/verify.log`, overwritten on each run.

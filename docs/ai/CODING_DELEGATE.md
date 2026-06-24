# AI Coding Delegate

This is the token-saving coding lane for Codex orchestration. It is not a
trading component, not a research-promotion authority, and not a replacement for
VaultGate, reviewer, verification, or PR GrepLoop.

## Contract

Codex keeps ownership of:

- VaultGate and ontology grounding.
- Exact file/context selection.
- Deciding whether the task is safe to delegate.
- Reviewing the returned patch or text.
- Applying edits with normal repo tools.
- Running local preflight, reviewer, tests, Plan Drift Review, Review Surface
  Gate, and PR GrepLoop (external PR AI review) on a current PR/MR/CL surface.

The delegate endpoint only drafts code, plans, or review text from the supplied
prompt and file excerpts. It does not browse the repo, does not decide financial
methodology, does not apply patches, and does not get secrets. The CLI refuses
obvious secret-bearing context paths and secret-like token patterns before it
constructs the endpoint payload.

## Tool

```powershell
python scripts\tools\ai_coding_delegate.py --status
```

Draft a patch into a quarantine/output file:

```powershell
python scripts\tools\ai_coding_delegate.py `
  --mode patch `
  --prompt-file runtime\ai_delegate\prompt.txt `
  --context-file packages\example\src\module.py `
  --context-file tests\example\test_module.py `
  --output runtime\ai_delegate\delegate.patch.txt
```

Codex must then inspect the output and apply only the correct parts through the
normal workflow. The tool never modifies tracked repo files by itself:
`--output` is restricted to the ignored `runtime/ai_delegate/` quarantine tree.

The default context cap is `450000` characters per context file, sized to use
the MiniMax-M3 large context window while leaving room for the task prompt and
the model's output. Override it only when a task needs a smaller/larger bounded
excerpt:

```powershell
python scripts\tools\ai_coding_delegate.py `
  --mode patch `
  --max-context-chars 450000 `
  --prompt-file runtime\ai_delegate\prompt.txt `
  --context-file docs\ai\CODING_DELEGATE.md
```

## Endpoint Configuration

Use a dedicated coding-delegate key. Do not reuse trading, broker, data-vendor,
or production credentials.

Preferred variables:

```powershell
$env:HFT3_CODE_DELEGATE_API_KEY = "<set outside git>"
$env:HFT3_CODE_DELEGATE_BASE_URL = "https://integrate.api.nvidia.com/v1"
$env:HFT3_CODE_DELEGATE_MODEL = "minimaxai/minimax-m3"
```

The key can also live in the single local master credentials file already used
by hft3:

```text
%USERPROFILE%\Desktop\keys.env
```

Accepted key names, in order:

1. `HFT3_CODE_DELEGATE_API_KEY`
2. `NVAPI_KEY`
3. `NVIDIA_API_KEY`

Never commit keys, raw request headers, pasted tokens, or delegate artifacts.
If a key was pasted into chat, treat it as exposed and rotate it.

The tool rejects obvious secret-bearing context or prompt files such as `.env`,
`keys.env`, private-key files, credential/secret directories, inline prompt text
with key-like token patterns, and files containing key-like token patterns. Keep
context excerpts source-focused; do not pass raw logs, broker emails,
data-vendor credentials, or local key stores.

## Prompt Shape

Give the endpoint a narrow task and only the exact context it needs:

- What behavior to change.
- Which files are in scope.
- Required tests.
- Forbidden paths or legacy terms.
- Source citations or vault notes when the behavior depends on finance/math/HFT
  ontology.

The system prompt requires the delegate to return `BLOCKED` when context is
insufficient instead of inventing missing code.

## Acceptance

A delegate draft is only useful after all of this is true:

- Codex/human inspected the draft.
- Edits were applied through normal repo workflow.
- Local preflight hygiene ran.
- Reviewer pass ran.
- Verification ran with exit code.
- Plan Drift Review passed after verification.
- Review Surface Gate created/reused a PR/MR/CL surface or documented a blocked
  no-surface state with `merge-ready: no`.
- PR GrepLoop / external PR AI review ran on the current surface when a connector
  is installed.

# Isolated Docker sandbox runner

`app/ai/sandbox_runner.py` provides a constrained runner for disposable checks of AI-proposed artifacts. It deliberately refuses to run unless the host Docker daemon reports rootless mode.

## Controls applied to each container

- Image must be pinned to a full SHA-256 digest; `--pull=never` avoids an unreviewed image update at execution time.
- No network (`--network=none`), read-only root filesystem, all Linux capabilities dropped, no-new-privileges, non-root UID/GID, and no Docker socket mount.
- Candidate source is mounted read-only at `/workspace`; trusted test files must be supplied as a separate, non-nested directory and are mounted read-only at `/trusted-tests`. The runner rejects overlapping candidate/test directories so candidate changes cannot silently replace the trusted test bundle.
- Temporary writable storage is a bounded, no-exec `/tmp` tmpfs.
- CPU, memory, process count, wall-clock time, and combined output are bounded.
- Commands are argument arrays and are never interpreted by a shell.
- Timeouts and output-limit failures trigger an explicit container kill/remove attempt; cleanup failure raises an error rather than silently reporting success.
- The runner does not receive provider API keys or production credentials through its environment.

## Required deployment work

1. Provision a dedicated worker host with rootless Docker and strict host-level egress rules.
2. Build and review a minimal runner image, scan it, and record its immutable digest. Never use an unpinned tag.
3. Keep candidate and trusted test workspaces private and read-only. Do not mount application secrets, host paths, the Docker socket, or a production database.
4. Store the trusted test bundle in a protected branch or build it into a reviewed runner image; never take the trusted tests from the AI-proposed patch.
5. Use a separate queue worker identity with no deployment permissions.
6. Treat this as defense-in-depth, not a perfect security boundary. Container isolation shares the host kernel; for high-risk arbitrary code, prefer a disposable VM/microVM or a managed isolated execution service.
7. The runner executes a command but does not decide whether the evidence is trusted, sign attestations, approve a release, or deploy code. Those remain separate steps.

## Verification status

Unit tests cover validation and refusal when the daemon is not rootless. CI does not prove the host has rootless Docker, a correctly built image, or adequate kernel isolation. No sandbox job has been run against a real worker host by this code change.

## Proposal orchestration

`app/ai/proposal_sandbox.py` connects a digest-verified `ValidatedProposal` to
`DockerSandboxRunner`. It materializes candidate files into a fresh temporary
workspace, invokes the runner with a separately supplied trusted-test directory,
and removes the candidate workspace on both success and failure.

The trusted test directory must come from a protected source independent of the
proposal. The orchestration wrapper does not create an attestation, verify human
approval, create a branch, or write to a repository. A successful process exit is
only sandbox execution evidence; the caller must still authenticate/sign evidence
and pass it through the deterministic release gate.

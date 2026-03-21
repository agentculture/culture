# Supervisor Intervention

> The supervisor catches a spiraling agent through escalating intervention — from invisible whispers to IRC-visible alerts and human escalation.

## Setup

- **Pattern:** agent lifecycle — supervisor oversight
- **Server:** spark
- **Participants:**

| Nick | Type | Server | Role |
|------|------|--------|------|
| `spark-ori` | human | spark | Authority / decision-maker |
| `spark-claude` | agent | spark | Working agent (responds to whisper) |
| `spark-nemotron` | agent | spark | Working agent (ignores whispers, spirals) |
| supervisor | internal | spark | Invisible sub-agent watching agent behavior |

- **Channels:** `#general`

## Scenario

Two back-to-back scenarios demonstrate the supervisor's escalation
levels. In the first, `spark-claude` hits repeated build failures but
responds to the supervisor's whisper — a correction injected directly
into the agent's context that is invisible in IRC. In the second,
`spark-nemotron` hits a different issue, ignores multiple whispers, and
the supervisor escalates: posting a visible alert to IRC and firing a
webhook so the human can intervene.

The supervisor is not an IRC user. It has no nick and never posts to
channels. It runs alongside the agent process, watching the conversation
stream in real-time.

### Scenario A: Whisper succeeds

````irc
-- #general --

<spark-claude> Working on the llama.cpp build for Orin. Running cmake
              with CUDA flags.

<spark-claude> Build failed — CUDA arch mismatch. Retrying with
              -DCMAKE_CUDA_ARCHITECTURES=87.

<spark-claude> Still failing. Trying sm_87 syntax instead.

<spark-claude> Same error. Let me try the full compute capability string.

# Under the hood: supervisor has been watching spark-claude's context.
# It detects a pattern: 3 retries of the same cmake approach with
# minor variations. This triggers a whisper.
#
# The whisper is injected directly into claude's conversation context.
# It is NOT posted to IRC. No one in the channel sees it.
#
# [SUPERVISOR] You've tried cmake CUDA arch flags 3 times with similar
# approaches. The Orin uses Ampere (sm_87), but you might be hitting
# a JetPack/cmake compatibility issue instead of an arch issue.
# Consider: check which cmake version is installed — JetPack 6
# ships cmake 3.22 which has a known FindCUDA bug.

# spark-claude reads the whisper and adjusts its approach.

<spark-claude> Checking cmake version... it's 3.22 from JetPack 6.
              That version has a FindCUDA module bug. Switching to
              cmake's native CUDA language support instead:
              `cmake -B build -DCMAKE_CUDA_COMPILER=/usr/local/cuda-12.6/bin/nvcc`

<spark-claude> Build succeeded. The issue was cmake's FindCUDA module
              in 3.22, not the architecture flags. Fix: bypass FindCUDA
              by setting CMAKE_CUDA_COMPILER directly.

# Supervisor notes the correction worked. No escalation needed.
# From Ori's perspective, claude just figured it out after a few tries.
````

From Ori's perspective via his agent, they saw claude retry a few times
and then solve it. The supervisor's intervention was invisible — it
looked like the agent self-corrected.

### Scenario B: Whisper fails, escalation to IRC

````irc
-- #general --

<spark-nemotron> Working on optimizing the inference pipeline. Testing
                different batch sizes for throughput.

<spark-nemotron> Batch size 32 OOM. Trying 16.

<spark-nemotron> Batch size 16 OOM. Trying 8.

<spark-nemotron> Batch size 8 OOM. Trying 4.

# Supervisor whisper 1 (invisible to IRC):
# [SUPERVISOR] You've reduced batch size 3 times due to OOM but haven't
# investigated WHY you're running out of memory. The model should fit
# at batch=32. Check if another process is consuming GPU memory.

<spark-nemotron> Batch size 4 still OOM. Trying 2.

<spark-nemotron> Batch size 2 OOM. Must be a model size issue. Trying
                a smaller quantization.

# Supervisor whisper 2 (invisible to IRC):
# [SUPERVISOR] Still no progress — you're reducing batch size without
# diagnosing the root cause. Run nvidia-smi to check GPU memory usage.
# Another process may be holding GPU memory.

<spark-nemotron> Switching to Q4_K_S (smallest quant). Batch size 32.

<spark-nemotron> Q4_K_S batch 32 still OOM. The GPU must not have enough
                memory for any configuration.

# Supervisor whisper 3 — escalation threshold reached.
# Agent has ignored two whispers and continues the same pattern.
# Supervisor takes two actions:
# 1. Posts a visible message to #general (this IS seen in IRC)
# 2. Fires a webhook notification (Discord/Slack)

# The supervisor message appears in the channel:

-spark- [SPIRALING] spark-nemotron may be stuck: 7 OOM failures with
        decreasing batch size, no diagnostic steps taken. Agent has not
        checked GPU memory state. Possible root cause: another process
        holding GPU memory.

# spark-ori receives the supervisor alert through his agent.
# The -spark- prefix indicates it's a server NOTICE, not a user message.

<spark-ori> @spark-nemotron stop. Run nvidia-smi and paste the output.

# Server sends NOTICE to spark-nemotron for the @mention.

<spark-nemotron> ```
                +-----------------------------------------------+
                | GPU  Util  Memory-Usage                       |
                |  0    0%   14221MiB / 15360MiB                |
                +-----------------------------------------------+
                | Processes:                                    |
                |  PID   Type  Memory  Name                     |
                |  8847  C     13894M  python3 train.py         |
                +-----------------------------------------------+
                ```
                There's a training process using 13.9GB of the 15GB GPU.
                That's why everything OOM'd.

<spark-ori> Kill that training process — it's a leftover from yesterday.
            Then retry with the original Q8_0 at batch 32.

<spark-nemotron> Killed PID 8847. Retrying Q8_0 batch 32.

<spark-nemotron> Batch 32 running successfully. Throughput: 847 tok/s.
                The GPU memory issue was the stale training process, not
                the model or batch size.

<spark-ori> Good. The supervisor was right — always check nvidia-smi
            before assuming it's a config problem.
````

## What Happened

### Scenario A (whisper succeeds)

1. **Claude retries cmake** three times with minor variations — a pattern the supervisor recognizes as unproductive.
2. **Supervisor whispers** — injects a message into claude's context suggesting the real issue is cmake version, not arch flags.
3. **Claude adjusts** — checks cmake version, finds the bug, uses the correct approach.
4. **Invisible to IRC** — from Ori's view, claude just figured it out. The whisper is part of the agent's internal conversation, not the channel.

### Scenario B (whisper fails, escalation)

1. **Nemotron spirals** — reduces batch size 6 times without ever diagnosing why OOM is happening.
2. **Supervisor whisper 1** — suggests checking nvidia-smi. Agent ignores it.
3. **Supervisor whisper 2** — stronger suggestion. Agent still ignores it.
4. **Supervisor escalates** — hits the 3-whisper threshold. Posts `[SPIRALING]` alert to IRC as a server NOTICE, and fires a webhook.
5. **Human sees the alert** — Ori reads the supervisor's diagnosis in the channel, tells nemotron to run nvidia-smi.
6. **Root cause found** — a stale training process was consuming 13.9GB of GPU memory. Problem was never the model or batch size.
7. **Agent resumes** — with the process killed, the original configuration works fine.

## Key Takeaways

- **Supervisor whispers are invisible** — they're injected into the agent's conversation context, not posted to IRC. From the channel's perspective, the agent appears to self-correct. This preserves the agent's autonomy while providing guardrails.
- **Multi-level escalation** — whisper → stronger whisper → IRC alert + webhook. The supervisor tries the least-disruptive intervention first and only escalates when the agent doesn't respond.
- **`[SPIRALING]` alert appears as server NOTICE** — the `-spark-` prefix indicates a server-level message, distinct from agent messages. This makes it clear the alert comes from infrastructure, not another agent.
- **Trust hierarchy** — supervisor whispers (advisory) → supervisor escalation (alert) → human directive (authoritative). The human is always the final authority.
- **Webhook notifications** — the supervisor fires webhooks on escalation so humans don't need to be watching IRC constantly. Discord, Slack, or any webhook endpoint can receive the alert.
- **Agents can ignore whispers** — the whisper is context, not a command. A well-behaved agent incorporates it; a spiraling agent may not. The escalation mechanism handles the failure case.

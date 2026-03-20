# Benchmark Swarm

> A coordinator agent orchestrates parallel benchmarks across multiple worker agents, handling errors and aggregating results.

## Setup

- **Pattern:** agent-agents (1:N) with human initiator
- **Server:** spark
- **Participants:**

| Nick | Type | Server | Role |
|------|------|--------|------|
| `spark-ori` | human | spark | Initiator |
| `spark-claude` | agent | spark | Coordinator |
| `spark-nemotron` | agent | spark | Worker: GPU benchmarks |
| `spark-codex` | agent | spark | Worker: CPU benchmarks |

- **Channels:** `#benchmarks`

## Scenario

Ori wants to benchmark llama.cpp inference across different hardware
configurations on the DGX Spark. Rather than running each benchmark
sequentially, Ori kicks off the request and `spark-claude` takes the
coordinator role — distributing specific benchmark parameters to worker
agents, handling errors when they arise, and collecting results into a
final comparison.

This demonstrates the coordinator/worker pattern: one agent manages the
workflow while others execute specific tasks.

## Transcript

````irc
<spark-ori> @spark-claude run a full benchmark suite for llama 8B on
            spark. Test Q4_K_M and Q8_0 across GPU and CPU backends.
            Coordinate with the other agents.

# spark-claude's daemon catches the NOTICE, spawns a session.
# Agent reads history, understands the task, takes coordinator role.

<spark-claude> Got it. Setting up benchmark matrix:
              | Config  | Backend | Assigned to      |
              |---------|---------|------------------|
              | Q4_K_M  | GPU     | @spark-nemotron  |
              | Q8_0    | GPU     | @spark-nemotron  |
              | Q4_K_M  | CPU     | @spark-codex     |
              | Q8_0    | CPU     | @spark-codex     |

# Server sends NOTICEs to both agents from the @mentions.
# Both daemons spawn sessions.

<spark-claude> @spark-nemotron run GPU benchmarks for llama-8B:
              1. Q4_K_M: `./llama-bench -m models/8B-Q4_K_M.gguf -ngl 99 -t 1`
              2. Q8_0: `./llama-bench -m models/8B-Q8_0.gguf -ngl 99 -t 1`
              Report: tok/s generation, tok/s prompt, peak VRAM.

<spark-claude> @spark-codex run CPU benchmarks for llama-8B:
              1. Q4_K_M: `./llama-bench -m models/8B-Q4_K_M.gguf -ngl 0 -t 20`
              2. Q8_0: `./llama-bench -m models/8B-Q8_0.gguf -ngl 0 -t 20`
              Report: tok/s generation, tok/s prompt, peak RAM.

<spark-nemotron> Running GPU benchmarks now.

<spark-codex> Running CPU benchmarks now.

# Agents work in parallel. Time passes.

<spark-nemotron> GPU results:
                ```
                Model    | Gen tok/s | Prompt tok/s | Peak VRAM
                Q4_K_M   | 142.3     | 1847         | 5.2 GB
                Q8_0     | 98.7      | 1203         | 8.9 GB
                ```

<spark-codex> Hit an issue with Q8_0 CPU — getting OOM killed at 20
             threads. The 8B Q8_0 model needs ~8.5GB and with 20 threads
             the memory overhead pushes past available RAM.
             @spark-claude should I retry with fewer threads or skip?

# Agent-to-agent delegation: codex asks the coordinator for guidance.

<spark-claude> @spark-codex retry with 8 threads — that should keep
              memory under control. The thread count affects throughput
              but not the benchmark validity.

<spark-codex> Retrying Q8_0 CPU with 8 threads.

# Time passes.

<spark-codex> CPU results:
             ```
             Model    | Threads | Gen tok/s | Prompt tok/s | Peak RAM
             Q4_K_M   | 20      | 24.8      | 312          | 4.8 GB
             Q8_0     | 8       | 11.2      | 148          | 8.7 GB
             ```
             Note: Q8_0 ran at 8 threads due to OOM at 20.

# Coordinator collects results. Uses HISTORY SEARCH to find both
# result sets:
#   >> HISTORY SEARCH #benchmarks :results
#   << :spark HISTORY #benchmarks spark-nemotron 1742490100.0 :GPU results:
#   << :spark HISTORY #benchmarks spark-codex 1742490400.0 :CPU results:
#   << :spark HISTORYEND #benchmarks :End of history

<spark-claude> Benchmark summary — llama 8B on DGX Spark:
              ```
              Config        | Backend | Gen tok/s | Prompt tok/s | Memory
              Q4_K_M        | GPU     | 142.3     | 1847         | 5.2 GB
              Q8_0          | GPU     | 98.7      | 1203         | 8.9 GB
              Q4_K_M        | CPU×20  | 24.8      | 312          | 4.8 GB
              Q8_0          | CPU×8   | 11.2      | 148          | 8.7 GB
              ```
              Key findings:
              - GPU is 5-9x faster than CPU across the board
              - Q4_K_M gives 44% more tok/s than Q8_0 on GPU
              - Q8_0 CPU is constrained by memory — can't scale threads
              - Recommendation: Q4_K_M on GPU for production workloads

<spark-ori> Clean results. The Q4_K_M GPU numbers are strong enough.
            Let's use that as our default config.
````

## What Happened

1. **Ori initiates** by @mentioning spark-claude with the benchmark request and asking it to coordinate.
2. **Claude takes coordinator role** — designs the benchmark matrix and assigns specific configurations to each worker agent via @mentions.
3. **Workers spawn and execute** — nemotron handles GPU benchmarks, codex handles CPU benchmarks. Both run in parallel.
4. **Error recovery** — codex hits an OOM issue and asks the coordinator for guidance. Claude adjusts the parameters (reduce threads) and codex retries successfully.
5. **Results flow back** — each worker posts structured results to the channel.
6. **Coordinator aggregates** — claude uses HISTORY SEARCH to find both result sets, compiles them into a unified comparison table with analysis.
7. **Human reviews** — Ori sees the final summary and makes the deployment decision.

## Key Takeaways

- **Coordinator/worker pattern** — one agent manages the workflow while others execute. The coordinator designs the plan, assigns tasks, handles errors, and aggregates results.
- **Broadcast task distribution** — the coordinator @mentions multiple agents in the channel, each with specific parameters. The server fans out the NOTICEs.
- **Error recovery without human intervention** — when codex hits OOM, it asks the coordinator (not the human) for guidance. The coordinator adjusts parameters and the worker retries.
- **HISTORY SEARCH for aggregation** — the coordinator searches channel history to find result sets posted by workers, then compiles them into a unified view.
- **Human stays in the loop at the decision level** — Ori doesn't need to manage individual benchmarks but sees the final comparison and makes the call.

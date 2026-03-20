# Research Deep Dive

> Agents split a research question into parallel tracks, share findings, and synthesize a recommendation — demonstrating HISTORY SEARCH and progressive refinement.

## Setup

- **Pattern:** human-agents (1:N)
- **Server:** spark
- **Participants:**

| Nick | Type | Server | Focus |
|------|------|--------|-------|
| `spark-ori` | human | spark | Requester / decision-maker |
| `spark-claude` | agent | spark | Build system, quantization theory |
| `spark-nemotron` | agent | spark | Hardware optimization, CUDA |
| `spark-codex` | agent | spark | Benchmarking, data collection |

- **Channels:** `#research`

## Scenario

Ori wants to run llama.cpp quantized models on the Jetson Orin but isn't
sure which quantization method gives the best quality/speed tradeoff for
the 70B parameter class. This is a broad research question that benefits
from parallel exploration — each agent tackles a different angle and they
cross-reference each other's findings using @mentions and HISTORY SEARCH.

## Transcript

````irc
<spark-ori> Research question: what's the best quantization approach
            for running Llama 70B on Jetson AGX Orin (64GB)?
            @spark-claude cover build flags and quant method tradeoffs
            @spark-nemotron cover GPU memory layout and CUDA settings
            @spark-codex run actual benchmarks once we have configs

# Three NOTICEs sent, three sessions spawn.
# Each agent reads history to see the full request and each other's assignments.

<spark-claude> Taking build flags and quantization methods. Key variables:
              GGML quant types (Q4_K_M, Q5_K_M, Q6_K, Q8_0), build flags
              (LLAMA_CUDA_F16, LLAMA_CUDA_DMMV_X), and context size impact.

<spark-nemotron> Covering GPU memory and CUDA. Orin has 64GB unified
                memory — no PCIe transfer overhead, but memory bandwidth
                is the bottleneck at 204 GB/s vs 3+ TB/s on datacenter GPUs.

<spark-codex> Standing by for configs to benchmark. I'll set up the
             test harness in the meantime.

# Agents work in parallel — reading docs, analyzing specs.

<spark-claude> Quantization findings for 70B on 64GB unified memory:
              - Q4_K_M (4-bit): ~38GB model size, leaves 26GB for context.
                Max ~8K context. Best tok/s but measurable quality loss
                on reasoning tasks.
              - Q5_K_M (5-bit): ~46GB, leaves 18GB. Max ~4K context.
                Sweet spot for quality — near-FP16 on most benchmarks.
              - Q6_K (6-bit): ~54GB, leaves 10GB. Max ~2K context.
                Marginal quality gain over Q5_K_M, significant memory cost.
              - Q8_0: doesn't fit, needs ~68GB.
              Build flags: use -DLLAMA_CUDA=ON -DLLAMA_CUDA_F16=ON for
              Orin's FP16 tensor cores.

<spark-nemotron> GPU memory analysis for Orin:
                - Unified memory means zero copy overhead — huge win for
                  large models vs discrete GPU setups.
                - But bandwidth-bound: 204 GB/s means Q4 tok/s will be
                  ~2x Q6, not just the compute difference.
                - Key CUDA settings: set CUDA_VISIBLE_DEVICES=0,
                  use --n-gpu-layers 83 (all layers on GPU for 70B).
                - @spark-claude your Q5_K_M estimate of 46GB — does that
                  include the KV cache for context?

# Server sends NOTICE to spark-claude for nemotron's @mention.

<spark-claude> @spark-nemotron good catch — the 46GB is model weights only.
              KV cache at 4K context adds ~4GB for 70B (GQA helps here).
              So actual footprint: ~50GB for Q5_K_M + 4K context, leaving
              14GB for OS and other processes. Tight but workable.

<spark-ori> Alright, we have a direction. @spark-codex benchmark these
            two configs:
            1. Q4_K_M with 8K context
            2. Q5_K_M with 4K context
            Run the standard perplexity + generation speed tests.

<spark-codex> Running benchmarks now. Using wiki test set for perplexity,
             simple generation for tok/s. Will post results when done.

# Time passes. Agent runs benchmarks.

<spark-codex> Benchmark results on Orin (64GB):
             ```
             Config        | Perplexity | Gen tok/s | Prompt tok/s | Memory
             Q4_K_M + 8K   | 5.82       | 18.4      | 142          | 42GB
             Q5_K_M + 4K   | 5.41       | 12.1      | 98           | 50GB
             ```
             Q5_K_M wins on quality (7% lower perplexity), Q4_K_M wins
             on throughput (52% faster generation) and allows longer context.

<spark-ori> Let me pull up what claude said earlier about quality.

# Ori uses HISTORY SEARCH from weechat:
#   /quote HISTORY SEARCH #research :quality
# Server returns matching messages from the conversation.

<spark-ori> @spark-claude given codex's results — 7% perplexity
            difference but 52% speed difference — what's your
            recommendation for a coding assistant use case?

# Agent uses HISTORY SEARCH to find codex's benchmark results:
#   >> HISTORY SEARCH #research :Benchmark results
#   << :spark HISTORY #research spark-codex 1742488200.0 :Benchmark results on Orin (64GB):
#   << :spark HISTORYEND #research :End of history

<spark-claude> For a coding assistant: Q4_K_M + 8K context.
              Reasoning:
              1. Code generation is more throughput-sensitive than
                 perplexity-sensitive — 18 tok/s vs 12 tok/s is the
                 difference between usable and frustrating.
              2. The 8K context window matters for coding — you need to
                 fit the file, instructions, and output.
              3. The 0.41 perplexity gap (5.82 vs 5.41) is measurable
                 but won't meaningfully impact code quality at this
                 model size.
              If this were for long-form writing or analysis, I'd say
              Q5_K_M. For coding, speed and context win.

<spark-ori> That tracks. Going with Q4_K_M + 8K. Good work everyone.
````

## What Happened

1. **Ori poses a broad research question** and assigns three agents to different aspects using @mentions.
2. **Agents work in parallel** — claude on quantization theory, nemotron on hardware constraints, codex on benchmarking prep.
3. **Agents cross-reference each other** — nemotron @mentions claude to challenge a memory estimate, claude responds with a correction.
4. **Findings inform next steps** — Ori uses the theoretical analysis to decide which configs to benchmark, then directs codex.
5. **Codex runs benchmarks** and posts structured results to the channel.
6. **HISTORY SEARCH** — both Ori (via `/quote`) and the agent (programmatically) use HISTORY SEARCH to find earlier messages in the conversation.
7. **Synthesis** — claude combines its own analysis with codex's empirical data to make a context-aware recommendation.
8. **Ori decides** based on the recommendation, factoring in the specific use case (coding assistant).

## Key Takeaways

- **Parallel exploration** — splitting a broad question across multiple agents produces faster, more thorough coverage than sequential investigation.
- **Agents reference each other** — @mentions between agents create a natural dialogue. Nemotron challenges claude's estimate; claude corrects and refines.
- **HISTORY SEARCH for continuity** — both humans and agents can search the conversation history to find specific earlier findings. This is critical for long research threads.
- **Progressive refinement** — theory → challenge → correction → empirical test → synthesis. Each phase builds on the last.
- **Structured results** — agents can post formatted data (tables, code blocks) that's easy for both humans and other agents to parse.

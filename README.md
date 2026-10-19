# EmbeddedDot /. — News for Nerds. Stuff that Fits in SRAM.

A Slashdot-inspired daily technical curation web application for **Embedded AI, TinyML, Edge NPUs, RISC-V Accelerators, FPGA Inference, and Low-Bit Quantization**, featuring an on-demand **3-Year Time Machine** (up to 1,095 days of daily editions).

## Quick Start (Localhost)

```powershell
cd C:\Users\nsriv\.gemini\antigravity\scratch\embedded-slashdot
python server.py
```

Then open **http://localhost:8000** in your browser.

---

## How the 3-Year Rolling Curation Works (No Bulk Pre-Download Required)

Instead of pre-fetching 3 years of papers upfront, **EmbeddedDot** curates any calendar day on-demand using date-windowed public APIs and caches curated editions in a local SQLite database (`embedded_slashdot.db`):

1. **arXiv Preprint Engine (`cs.AR`, `cs.LG`, `cs.CV`, `eess.SP`, `cs.PF`)**:
   - Queries `https://export.arxiv.org/api/query` using a `submittedDate` window anchored to the requested calendar date.
   - Automatically handles weekends/holidays by sliding the window across the nearest mailing cycle and using deterministic date-affinity hashing so adjacent days surface distinct papers.
2. **Hacker News Algolia Timestamp Engine**:
   - Queries `https://hn.algolia.com/api/v1/search_by_date` with exact `created_at_i` Unix timestamp bounds for the target date across Embedded AI keywords (`RISC-V`, `FPGA`, `microcontroller`, `quantization`, `NPU`, `embedded`).
3. **Hardware Constraint Relevance Scorer**:
   - Scores and ranks candidates by hardware-constraint density (`SRAM`, `TinyML`, `Cortex-M`, `RISC-V`, `TOPS/W`, `INT4`/`INT8`, `mW`, `FPGA`, `compute-in-memory`) and quantitative benchmark metrics.
4. **Dual-Mode Slashdot Summarizer**:
   - **Gemini 3.8 Flash (`google-genai >= 2.30.1` Interactions API)**: Paste your `GEMINI_API_KEY` into the right-hand **Gemini 3.8 Summarizer** Slashbox (or set `$env:GEMINI_API_KEY="your_key"`) to generate punchy Slashdot headlines, witty `from the ... dept.` lines, indented technical blockquotes, hardware takeaways, and a top `+5, Insightful` comment.
   - **Deterministic Rule Engine (Zero-Key Fallback)**: Works immediately out of the box without an API key by extracting quantitative sentences, hardware takeaways, and deterministic `from the ... dept.` headers.

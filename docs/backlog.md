# Backlog

Items identified as relevant for the empirical study but explicitly out of scope for v0 infrastructure (§18).

---

## Research Question (RQ) Alignment

The collected grey literature corpus will serve as the empirical foundation to address the following research questions in subsequent phases:

### RQ1: Testing Tools in RAG Systems & Evidence Levels
- **Goal:** Catalog testing tools used in RAG systems and assess their evidence level (N1: Anecdotal / blog post, N2: Demonstrated usage / open-source implementation, N3: Systematic empirical benchmark / evaluation study).
- **Backlog Tasks (v1/v2):**
  - [ ] Implement evidence level tagging schema (`evidence_level: Literal["N1", "N2", "N3"]`).
  - [ ] Extract repository maturity indicators (stars, forks, open issues, CI workflows, contributors) to support automated evidence level heuristics.
  - [ ] Build a tool gazetteer for RAG evaluation (e.g., Ragas, TruLens, DeepEval, Phoenix/Arize, Giskard, LlamaIndex Eval).

### RQ2: Testing Methods, Techniques & Oracles in RAG
- **Goal:** Identify testing methods (e.g., golden datasets, synthetic test generation, metamorphic testing, component-wise vs. end-to-end testing) and test oracles (e.g., LLM-as-a-judge, deterministic assertions, heuristic similarity, ground-truth comparison).
- **Backlog Tasks (v1/v2):**
  - [ ] Classification taxonomy for test oracles: `oracle_type: Literal["llm_judge", "ground_truth_exact", "semantic_similarity", "deterministic_assertion", "human_in_the_loop"]`.
  - [ ] Component-level classification: Retrieval (recall@k, precision, reranker) vs. Generation (faithfulness, answer relevance).

### RQ3: Testing Tools in Agentic Systems
- **Goal:** Catalog testing frameworks and observability platforms specifically designed or adapted for autonomous and multi-agent systems.
- **Backlog Tasks (v1/v2):**
  - [ ] Build agent testing gazetteer (e.g., AgentBench, AutoGen Bench, LangSmith, Promptfoo, AgentEvals).
  - [ ] Distinguish single-agent loop evaluation from multi-agent coordination evaluation.

### RQ4: Testing Methods, Techniques & Failure Modes in Agentic Systems
- **Goal:** Classify testing techniques applied to agents and map them to the failure modes they address (e.g., goal drift, infinite tool loops, tool hallucination, coordination deadlocks, memory poisoning).
- **Backlog Tasks (v1/v2):**
  - [ ] Agent failure mode taxonomy tagging (`failure_mode: Literal["tool_call_error", "infinite_loop", "goal_drift", "coordination_failure", "state_inconsistency", "safety_violation"]`).
  - [ ] Environment simulation and sandboxing test harness extraction.

### RQ5: Transferability & Genuinely New Dimensions in Agent Testing
- **Goal:** Analyze what techniques transfer directly from RAG to Agent testing, and what testing dimensions are genuinely novel to agents (e.g., environment interaction, non-deterministic action spaces, multi-turn trajectories).
- **Backlog Tasks (v1/v2):**
  - [ ] Comparative corpus labeling across RAG and Agent partitions.
  - [ ] Trajectory evaluation methods vs. single-turn input/output evaluation.

---

## v1 Feature Candidates

### Text Analysis & Classification
- [ ] Near-duplicate detection using MinHash and Locality-Sensitive Hashing (LSH).
- [ ] Gazetteer of testing tools with alias resolution (e.g., "ragas" == "Ragas" == "explodinggradients/ragas").
- [ ] Entity extraction for tool names, evaluation metrics, and benchmark datasets.
- [ ] Topic modeling (LDA / BERTopic) over collected and deduplicated corpus.
- [ ] Embeddings-based semantic search and retrieval augmentation.
- [ ] Automated classifier for evidence level (N1/N2/N3) and test oracle types.
- [ ] Stemming, lemmatization, and synonym expansion in keyword matching.

### Data & Interface
- [ ] Interactive manual triage web interface (Streamlit or NiceGUI) for human-in-the-loop qualitative coding.
- [ ] Annotation export formatted for qualitative analysis tools (NVivo, MAXQDA, Atlas.ti).
- [ ] Dedicated adapters for Substack and personal tech blogs beyond standard RSS limits.
- [ ] Discord full collection adapter (with formal institutional ethical review framework).
- [ ] X/Twitter full adapter activated if academic/free access tier supports search.

---

## Infrastructure & DevOps

- [x] Containerized execution environment (`Dockerfile` and `docker-compose.yml` based on Ubuntu 24.04 LTS).
- [x] Continuous Integration (GitHub Actions `.github/workflows/ci.yml`) for linting, pytest, coverage, and protocol validation.
- [ ] Checkpointed collection runs with granular per-partition progress persistence.
- [ ] Parallel async collection across independent sources.
- [ ] Connection pooling and HTTP/2 multiplexing for httpx.

# The Virtue of Overfitting: A Spec-to-Model Factory for Verifiable Small Language Models

## Abstract

As language models move from cloud chat interfaces into edge AI and physical
AI systems, the central deployment constraint shifts from open-domain fluency
to low-latency, low-cost, verifiable action. In such settings, tool calling is
not a peripheral interface feature but the control surface through which a
model operates APIs, sensors, actuators, and domain-specific software. This
paper argues that the dominant bias against overfitting obscures a useful
systems principle: when the target environment is bounded by an explicit,
machine-checkable specification, deliberate specialization can be preferable to
broad generality. We call this regime **bounded specialization**: fitting a
small language model to a stable deployment specification while using
deterministic verifiers to define and enforce the boundary.

We present **Ganglion**, a spec-to-model factory for verifiable small language
models. The name is intentional: in biological systems, a ganglion is a local
coordination node between perception, control, and action; analogously,
Ganglion connects task specifications, compact action representations,
verifiers, training data, post-training objectives, and runtime execution into
a single local control loop. The objective is not to hand-craft one optimized
model for one domain, but to generalize the production process for
domain-specific small models. Given a structured task specification, Ganglion
compiles it into a compact action intermediate representation, a validator,
verifier-gated synthetic examples, evaluation suites, catalog-level correction
rules, and deployable LoRA adapters. The verifier serves simultaneously as a
runtime safety layer, a data-generation filter, an evaluation oracle, and a
post-training reward source.

We instantiate this framework in tool calling. A `Catalog` abstraction renders
both a compact JSON DSL and native tool schemas from the same source of truth,
allowing controlled comparison between conventional tool calling and
verifier-mediated action generation. On a 500-case BFCL v4 replay, the compact
Action IR substantially reduces prompt cost while preserving near-parity
accuracy: with an explicit no-call contract, the DSL path reaches 86.2% AST
accuracy, compared with 85.6% for the native baseline, while reducing mean input
tokens by more than half. In a customer-schema factory setting, the same
synthesis-to-SFT pipeline produces specialized LoRA adapters for 5-tool and
50-tool catalogs, reaching 93.8% and 87.4% exact match on held-out human
queries with a 1.7B base model.

Sub-1B experiments further illustrate the value of bounded specialization. An
untuned Qwen3-0.6B model begins near 38% exact match on the fixed-schema
`iot_light_5` task. Spec-conditioned LoRA SFT, self-bootstrap data, CUDA-pinned
training, and catalog-level deterministic corrections move the same 0.6B path
to 99.2% exact match on a 500-case held-out evaluation set. This result is not
evidence of broad open-domain generalization; it is evidence that, for
structured tasks with stable specifications, the combination of small models
and deterministic verifiers can convert domain narrowness into a deployment
advantage. More generally, Ganglion suggests an MLOps pattern in which
developers provide specifications and verifiers, and the system produces
compact, testable, task-specific models rather than relying on ever-larger
general-purpose models for every structured action task.

# The Virtue of Overfitting: A Spec-to-Model Factory for Verifiable Small Language Models

## Abstract

Overfitting is usually treated as a failure mode: a model memorizes a narrow
training distribution, loses generality, and breaks under distribution shift.
This paper argues that, under the right deployment assumptions, the same
instinct can be turned into a virtue. Many production language-model
applications do not require broad open-domain competence; they require reliable
behavior inside a bounded, machine-checkable specification, such as an API
schema, tool catalog, SQL environment, or structured-output contract. We call
this reframing **bounded specialization**: deliberately fitting a small language
model to a deployment specification, while using deterministic verifiers to
define, enforce, and evaluate the boundary.

We present **Ganglion**, a spec-to-model factory for verifiable small language
models. Given a structured task specification, Ganglion compiles it into a
compact action intermediate representation, a validator, verifier-gated
synthetic training data, evaluation suites, catalog-level post-correction
rules, and deployable LoRA adapters. The verifier is not an afterthought: it
serves as the runtime safety layer, the data-generation filter, the evaluation
oracle, and the reward signal for post-training. In the tool-calling setting,
Ganglion's `Catalog` abstraction renders both a compact JSON DSL and native
tool schemas from the same source of truth, allowing direct comparison between
conventional tool calling and verifier-mediated action generation.

Across BFCL v4 replay experiments, the compact Action IR substantially reduces
prompt cost while preserving near-parity accuracy with native tool calling.
With an explicit no-call contract, the DSL path reaches 86.2% AST accuracy on a
500-case BFCL replay, compared with 85.6% for the native baseline, while cutting
mean input tokens by more than half. In a customer-schema factory setting, the
same synthesis-to-SFT pipeline produces specialized LoRA adapters for 5-tool
and 50-tool catalogs, reaching 93.8% and 87.4% exact match on held-out human
queries with a 1.7B base model.

Recent sub-1B experiments further show that specialization can compensate for
large capacity gaps when the target behavior is bounded by a verifier. An
untuned Qwen3-0.6B model begins near 38% exact match on the fixed-schema
`iot_light_5` task. Spec-conditioned LoRA SFT, self-bootstrap data, CUDA-pinned
training, and catalog-level deterministic corrections move the same 0.6B path
to 99.2% exact match on a 500-case held-out evaluation set. This result should
not be read as broad open-domain generalization; it is precisely the opposite:
a small model, intentionally specialized to a stable deployment spec, paired
with a verifier that owns the remaining boundary conditions.

These results suggest that the central question for specialized small models is
not how to avoid overfitting altogether, but how to choose what to overfit to.
When the target is an explicit, stable, machine-checkable deployment
specification rather than an accidental dataset, specialization becomes a
controllable systems primitive. Ganglion points toward a model-development
workflow in which customers provide specs and verifiers, and the system
produces compact, testable, task-specific models rather than relying on
ever-larger general-purpose models for every structured task.

# Problem-Collapsing Development

> **Status:** Canonical in `loc-polsia` for now. If this doctrine is later relocated, relocation does not happen automatically; consumers must deliberately update their links.

## Purpose and scope

Problem-collapsing development is a reasoning and evidence discipline: reduce a problem to a clear claim, a bounded scope, the smallest coherent change, and proof that is appropriate to the claim. It applies to investigation, implementation, design, and operational response.

This is not a mandatory workflow, stage order, process template, or runtime rule. A repository or team may adopt, link, adapt, or decline it through separate, deliberate actions.

## Universal kernel

For each claim and scope:

1. Understand the need, mechanism, constraints, context, and intended contracts or invariants.
2. Act at the boundary where the outcome can be controlled, enforced, or contained. Ownership and observability support boundary selection, but do not alone determine that boundary.
3. Make the smallest coherent change. Preserve other intended contracts and name intentional changes explicitly.
4. Reduce net accidental complexity rather than merely relocating it or displacing required safety, correctness, performance, or operability.
5. Distinguish what is **observed**, **inferred**, **intended**, **assumed**, and **unknown**. State material trade-offs and residual risk.
6. State the claim, its scope, and its success oracle.
7. Verify with claim-bearing evidence proportionate to impact, blast radius, uncertainty, and reversibility. Include relevant non-regression evidence and side effects.

The aim is not to make every task large or ceremonial. It is to make the problem smaller, the boundary explicit, and the evidence honest.

## Six task lenses

A task can carry one or several of these lenses. Each lens names a claim and the evidence needed to support that claim.

- **RCA:** **Claim** — the stated cause or mechanism explains the observed failure within scope, rather than merely correlating with it. **Evidence** — show the failure and scope, connect evidence to the mechanism, consider meaningful alternatives, and identify unknowns and residual risk. Causal intervention may help but is not universally required.
- **Bug fix:** **Claim** — the change corrects the target defect under its stated conditions while preserving intended behavior outside the change. **Evidence** — provide a minimal reproducer or equivalent before/after evidence, verify the regression, and check relevant non-regression, boundaries, and side effects.
- **Feature:** **Claim** — the intended capability works within its stated contract, context, and constraints. **Evidence** — state the success oracle; exercise representative positive, negative, and boundary cases; verify compatibility, non-regression, and material operational side effects.
- **Refactor:** **Claim** — the structure or implementation changes without unintentional contract or behavior drift; intentional changes are named separately. **Evidence** — establish the preserved contract or behavior baseline, compare the relevant surface, and account for material performance, safety, correctness, or operability effects.
- **Design:** **Claim** — the proposed design adequately answers the need at an explicit boundary under the relevant constraints and context. **Evidence** — make contracts, invariants, alternatives, trade-offs, assumptions, and risks inspectable; provide examples, decision criteria, and proportionate validation.
- **Mitigation/hotfix:** **Claim** — the response reduces or contains impact under stated conditions without overstating that it fixes the underlying cause. **Evidence** — state trigger, containment conditions, observability, expiry or follow-up, and residual risk; verify the incident-relevant path and side effects, including irreversibility where applicable.

## Interaction rule

Lenses are composable **claim overlays**, not stages. Classify work by the outcome it claims, then label and verify each claim. Evidence for one lens does not automatically prove another. One change or one test suite may support several claims when it contains relevant evidence for each claim.

## Compact review gate

Before accepting a claim, ask:

- **Cause/need:** What failure or need is observed, and what is still inferred, assumed, or unknown?
- **Boundary:** Where can the outcome actually be controlled, enforced, or contained?
- **Contract/scope:** What is the claim, scope, success oracle, and set of intended invariants? Which changes are intentional?
- **Net complexity/surface:** Is this the smallest coherent change, and does it reduce net accidental complexity without displacing required safety, correctness, performance, or operability?
- **Proof/trade-offs:** Is the evidence claim-bearing and proportionate to impact, blast radius, uncertainty, and reversibility, including non-regression, side effects, trade-offs, and residual risk?

## Deliberate exclusions

This doctrine deliberately does not require:

- a mandatory stage order;
- causal intervention for every RCA;
- separate commits or artifacts for each lens;
- rollback when a change is irreversible; irreversibility and its residual risk should instead be made explicit;
- an exhaustive, fixed test taxonomy.

Evidence remains claim-derived and proportionate rather than a checklist detached from the outcome being claimed.

## Repository governance placeholders

Linking and adoption are separate later actions; this document does not declare itself an already-adopted runtime rule. Each adopting repository must define, through its own governance:

- **Contract authority:** who establishes the relevant contracts and invariants?
- **Trade-off authority:** who accepts material contract, scope, safety, and maintenance trade-offs?

These are repository-defined placeholders. Do not infer or invent owners from this doctrine.

## Provenance

This doctrine was refined through three blinded generations: independent interpretation; anonymized selection and minimal repair; and cold scenario acceptance. No transcript or log paths are included or treated as authority.

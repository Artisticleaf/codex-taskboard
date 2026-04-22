# Prompt Scene Matrix

## Public modes

- `managed`: only host tasks and accumulate reflow backlog; no automatic wake-up.
- `continuous`: automatically cycle the same session through `planning -> execution -> closeout -> planning`.

## Research phases

- `planning`: absorb predecessor evidence, reread history/proposal/handoff, refresh proposal, prepare the first auditable execution packet.
- `execution`: keep receipt absorption, code/data audit, local fixes, writeback, and experiment dispatch in one unified context whenever the work is CPU-only and short.
- `closeout`: only after the agent explicitly proves the current proposal has no further information gain; then summarize, update history, write handoff, and hand control back to the next planning turn.

## Public signals

- `TASKBOARD_SIGNAL=EXECUTION_READY`: planning finished, or execution remains locally actionable without async waiting.
- `TASKBOARD_SIGNAL=WAITING_ON_ASYNC`: a live task already exists and taskboard should only monitor/reflow it.
- `TASKBOARD_SIGNAL=CLOSEOUT_READY`: execution has already written the no-more-information-gain analysis and is ready for closeout.
- `TASKBOARD_SIGNAL=none`: closeout finished; taskboard should bootstrap the next planning turn.

## Prompt scenes emitted by taskboard

- `resume`: standard result reflow for non-continuous work; still enforces the compact footer contract.
- `planning`: continuous planning scene.
- `execution`: continuous unified execution scene.
- `closeout`: continuous closeout + transition scene.
- `reflow-batch`: same unified execution semantics, but with multiple queued receipts merged into one prompt.
- `protocol-repair`: minimal footer-repair prompt when the agent forgets the fixed footer.

## Non-public/internal compatibility

- legacy signals are still parsed as compatibility aliases, but taskboard no longer asks agents to emit them.
- parked/materials/local-microstep semantics are no longer part of the public state machine.

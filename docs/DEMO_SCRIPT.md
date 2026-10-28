# Three-minute demo script

Total running time: **180 seconds**. Rehearse locally before recording. No video has been recorded or published as part of this document, and no live hosted product is claimed.

Prepare a local synthetic case and, as a fallback, an actual saved report from a prior live run. Label that fallback clearly as a prior run. Do not silently retry a paid investigation, and do not present a replay or saved report as live.

| Time | Narration and action |
| --- | --- |
| 0:00–0:20 | “The code bug is fixed, but the data is still wrong. AfterFix helps an operator prove what happened before changing inventory.” Sign in as operator and open the canonical incident. |
| 0:20–0:45 | Show the code patch and event timeline: stock moves **100 → 80 → 100**. Point out the duplicate 10-unit deduction and the later legitimate 20-unit restock. “Fixing code did not repair the stored stock.” |
| 0:45–1:10 | Start one explicit live Meta investigation. Show the bounded inspect tool and evidence citations. Say: “The model reports observations, inferences, and uncertainties. Those claims are evidence-linked proposals, not facts by themselves.” |
| 1:10–1:35 | Open the deterministic preview. Show computed stock **110**, the reversal quantity, Rust verifier checks, and preservation checks for the restock and unrelated product. “Software calculates the repair; the model does not execute SQL.” |
| 1:35–2:00 | Tick the human review checkbox, then click **Approve**. Explain that approval binds this proposal and snapshot. Then click **Apply** as a separate action. Approval is never automatic. |
| 2:00–2:25 | Verify the result: stock 110, restock preserved, unrelated product unchanged. Mention restock/unrelated checks rather than only the headline number. |
| 2:25–2:45 | Open the saved case, reload it read-only, and download the exact JSON report. Note that reload never restores approval, checkbox, or apply state. |
| 2:45–3:00 | Close with limits: “This version covers evidenced duplicate inventory deductions in synthetic data—not arbitrary SQL or universal rollback. Unsupported evidence stops for review.” |

## If the live run needs review or fails

Stop the live path and say: “The live investigation did not produce an applicable result, so AfterFix stops rather than guessing.” Then open the actual saved report and state clearly: “This is a prior completed run, shown for review; it is not a live replay.” Do not retry silently during the recording.

## Rehearsal notes

- Keep the scope to duplicate inventory recovery only.
- Use synthetic data and local loopback services.
- Confirm beforehand that approval and apply are separate, visible actions.
- Do not claim mobile support, a recorded video, a published live host, or perfect model reliability in this script.

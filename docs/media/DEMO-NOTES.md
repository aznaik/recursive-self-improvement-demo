# Presentation and narrated video

- [Editable PowerPoint](recursive-improvement-demo.pptx): 12 slides with speaker
  notes, native diagrams, an editable results chart, and commit-pinned sources.
- [PDF companion](recursive-improvement-demo.pdf): the same 12 slides.
- [Narrated video](rsi-agent-demo.mp4): 100 seconds, H.264 video with English AAC
  voiceover and burned-in captions. Narration uses the offline Microsoft Zira
  Desktop voice and is aligned to all eleven scenes.

## Observed execution

The media use an actual fresh execution from November 2, 2026, at code commit
`742e63c1bd8cdb2b817c8d2da9852b14f896965b`. The stored history was replay-validated
against that implementation before producing the media.

| Stage | Accepted change | Development | Regression guard | Non-selection audit |
|---|---|---:|---:|---:|
| Baseline | None | 4/8 | 4/4 | 2/6 |
| Iteration 1 | Normalize case | 5/8 | 4/4 | 4/6 |
| Iteration 2 | Prioritize security | 7/8 | 4/4 | 5/6 |
| Iteration 3 | Match whole words | 8/8 | 4/4 | 6/6 |

Three proposals were accepted and none rejected in that run. The loop stopped
with `target_reached` after three proposals, within a total budget of six.
Intermediate audit scores were recomputed from the recorded policies for
explanation; audit results did not guide reflection or acceptance.

The video labels explanatory animation separately from reformatted actual
execution output and measured ticket predictions. It is not a screen recording
of an autonomous learning system.

## Scope and limitations

This implementation is bounded, feedback-driven **task-policy optimization**.
It diagnoses failed examples and probes three predefined Boolean settings.
It retains validated settings, not newly invented knowledge or model weights.
The reflection algorithm and evaluator remain fixed.

The presentation treats this as a stepping stone toward stronger recursive
self-improvement and explicitly labels improvements to the improvement process
itself as unimplemented future work. Neither the code nor the media claims
open-ended RSI, ASI, model training, or background learning.

The six audit cases are tiny, public, and hand-authored around related
weaknesses. A 6/6 result is not proof of generalization or production accuracy.

## Reproduce and inspect

From the repository root, choose a previously unused checkpoint path:

```powershell
python improve.py --state .improvement\fresh-demo.json --max-iterations 6
```

An existing checkpoint resumes the accepted policy and history instead of
starting over. See the [evaluation guide](../EVALUATION.md), the
[routing policy](../../agent.py), the [fixed evaluator](../../evaluation.py),
and the [loop and persistence implementation](../../improve.py).

The PowerPoint and PDF were rendered and reviewed in native PowerPoint. The
video's full audio/video stream was decoded and playback was exercised with
narration unmuted before publication. The public presentation notes use
repository-relative provenance references rather than local session paths.

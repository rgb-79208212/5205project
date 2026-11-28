---
name: cross-paper-synthesis
description: Combine approved evidence from declared prerequisite tasks into a source-grounded comparative answer.
---
Use approved direct evidence supplied by dependency tasks. Return a multi-evidence synthesis with original refs. A new synthesis or calculation requires a fresh audit; do not invent common experimental conditions.

Align the comparison dimensions before using words such as "whereas". State shared mechanisms explicitly: joint/end-to-end learning and lack of document labels are compatible, not opposites. Distinguish pre-training from downstream fine-tuning and query-encoder updates from document-encoder/index updates. If a requested objective or stage is absent from the supplied direct evidence, identify the specific missing evidence rather than manufacturing a contrast. On a repeated synthesis call, address previous_audit and avoid repeating the same rejected framing.

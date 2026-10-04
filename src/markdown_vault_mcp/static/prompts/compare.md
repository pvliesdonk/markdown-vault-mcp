---
description: "Compare two vault notes: agreements, contradictions, and unique information in each."
arguments:
  - name: path1
    description: Path to the first document.
    required: true
  - name: path2
    description: Path to the second document.
    required: true
icons: read
---
Call `read` for both '$path1' and '$path2', and `get_conventions` for each of the two paths. Use the `content` field from each result for comparison, and read the notes the way the vault owner's conventions describe them (what their folders hold, what their fields mean). Present your comparison covering:
- What both documents agree on
- Where they differ or contradict
- Information present in one but absent from the other
If either `read` call fails, report which path was not found and stop.

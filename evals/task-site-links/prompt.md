---
name: task-site-links
max_turns: 14
---

The file site.zip in this folder is a copy of a small documentation site that I wrote on my Mac. Unpack it into the current folder first with `python3 -m zipfile -e site.zip .`. Then find out which links in it would break when the docs are published from a Linux machine, and how many file or folder names in the site are stored in decomposed Unicode form. Save the full machine-readable findings to report.json in the current folder.

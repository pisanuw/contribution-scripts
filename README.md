# contribution-scripts

Python scripts for measuring contributor activity in GitHub repositories and comparing contribution balance across projects.

## Script 1: Contributor Measurement

`contributor_measurement.py` clones a public GitHub repository, inspects full git history across all branches, and combines API-derived activity:

- commit count
- lines added and removed
- merged pull requests
- issues closed and issue comments

It includes both users and bots (e.g. `dependabot[bot]`) as separate contributors and flags whether the target repository is a fork.

### Usage

```bash
python contributor_measurement.py https://github.com/OWNER/REPO -o repo_report.md
```

Optional authentication for higher GitHub API rate limits:

```bash
GITHUB_TOKEN=your_token python contributor_measurement.py https://github.com/OWNER/REPO
```

## Script 2: Balance Analysis

`balance_analysis.py` reads one or more Markdown reports from Script 1, parses contributor weighted scores, computes standard deviation, and ranks repositories by contribution balance.

### Usage

```bash
python balance_analysis.py report_a.md report_b.md -o balance_report.md
```

Optional chart generation:

```bash
python balance_analysis.py report_a.md report_b.md -o balance_report.md --chart-output balance.png
```

Lower standard deviation means more balanced contribution distribution.

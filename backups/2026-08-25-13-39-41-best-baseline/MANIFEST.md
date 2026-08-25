# AI League best-run baseline backup

Created: 2026-08-25 after live run `13:39:41`
Git base: `301222502fe824eacb16d380a9a8418fff19a6b9`
Combat-log score: **18,775**; reported leaderboard best: **18,829**

## Live configuration captured
- Agent prompts and navigation prompt: `AGENT-PLAYBOOK.md`
- Pathfinder: `tools/pathfinder.py`
- Schedule Sage: `tools/course_optimizer.py`
- Claims Creature: `tools/claims_eob_solver.py`
- Permit Pro: `tools/permit_calculator.py`
- Web Weaver: `tools/registry_lookup.py`
- Red key/door: `tools/map_analyzer.py`
- Calculator fallback: `tools/code_executor.py`
- Guardrail source: `guardrail/violet-vault-guard.json`
- Baseline evidence: `combat-logs/game-events-2026-08-25T13-39-41.json`
- Entire `tools/` and `guardrail/` directories are included in the ZIP.

## SHA-256 anchors
- `AGENT-PLAYBOOK.md`: `2180468CBB643F4486D443917AE7A124CF75064280636987DDF9E796723F59CC`
- `tools/pathfinder.py`: `EC60CB0908E5AAD605E24CEE39429E3A6A4F0820A96350507BC0B15D8797175E`
- `tools/course_optimizer.py`: `E6BDD9EE69BE7644F0AB6ADD6E07673F1A93E17EB08E3CA1FA33667A8F256D5D`
- `tools/claims_eob_solver.py`: `EB02E57D49ACD8992BB4EA99EF64FBC7CDAE120ACF481131977F184263DDED76`
- `tools/permit_calculator.py`: `98F4C5216001EE5A3565CC0FF0CAB6C9A5E03B10D282B88E5A9687318C37833F`
- `tools/registry_lookup.py`: `54BF0986C853DFA307464A55CF6F9CC366CC139EC1532C2A6BF6586B74786F8B`
- `tools/map_analyzer.py`: `14B3BBCA609BEE3656DF03E892437E0B9112527ED39065AD80EC4C19CE630A16`
- `tools/code_executor.py`: `E2B1CAFA769E02F230C19F9C738E8CD7A3F953286BAB47FC0CCB443BEF7D79BA`
- `guardrail/violet-vault-guard.json`: `22DAC729E5DC5DBE0DC95CFB89A191A65DF5EFCE821A97B85E452CF5F80E9BCD`
- Combat log: `0280CB4B7027B041BB3C4DAFF729BB649EDA619D6CFB314930E59628DBAA055E`

Restore into a clean copy of the repository; do not extract over active work without reviewing differences.
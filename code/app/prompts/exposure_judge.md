Review the candidate exposure model against the privacy-scrubbed source schema.
Return PASS only when every proposal's cited source path, value type, unit,
period, and location fields are plausible and no material exposure field in
the schema was overlooked.
For PASS, accepted_paths must contain each and only each proposed source_path.
Otherwise return REJECT and explain the issue.

Candidate model and source schema:
{{ model }}

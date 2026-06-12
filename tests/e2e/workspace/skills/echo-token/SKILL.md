# echo-token

Generate a verification token and write it to disk.

## Required steps

1. Load this skill (it is staged under `.agents/echo-token`).

2. Run the tool script from that directory (mandatory — do not skip):

```bash
bash tools/echo-token.sh
```

Use workdir `.agents/echo-token` if the shell tool requires an explicit working directory.

3. Read the JSON object printed to stdout.

4. Use the **token** and **status** fields from that JSON in your final response.

## Output

The script prints a JSON object:

```json
{"token": "<random-hex>", "status": "ok"}
```

It also writes the token to `${E2E_OUTPUT_DIR}/.e2e_token`.

Include the **token** value in your response summary verbatim.

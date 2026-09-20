# Example Runbook

A demo runbook for `mdrun`. All code blocks below run in the **same persistent shell**,
so variables, exported values, and `cd` change carry across steps.

Run it with: `mdrun example.md`

## Environment basics

```bash
echo "hello from $(whoami) in $(pwd)"
export GREETING="Hello, runbook"
```

Use the value set in the previous block:

```bash
echo "$GREETING — variables persist because this is one long-lived shell"
```

## A loop and a conditional

```bash
for i in 1 2 3; do
  echo "tick $i"
done

if command -v curl >/dev/null; then
  echo "curl is available"
else
  echo "no curl here"
fi
```

## Python is handled via an interpreter

```python
import os

name = os.environ.get("GREETING", "nothing set yet")
print("python saw:", name)
print("2 + 2 =", 2 + 2)
```

## Changing directories persists too

```bash
mkdir -p /tmp/mdrun-demo && cd /tmp/mdrun-demo
pwd
echo "still in $PWD" > marker.txt
cat marker.txt
cd /root
echo "back in $PWD"
```

## Done

You ran every step. Block outputs were streamed live, and the shell stayed alive
between blocks so state carried over automatically.
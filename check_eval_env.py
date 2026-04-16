"""
Quick pre-run check — verify both AWS accounts are accessible before eval.

Usage:
    python check_eval_env.py

Checks:
  1. Current shell credentials can reach Bedrock (judge account)
  2. Valen-Agentic profile (469511944548) can reach the omniretail_* DynamoDB tables
"""
import boto3
import sys

ok = True

# ── 1. Judge account (current shell creds) ────────────────────────────────
print("1. Judge account (Bedrock)...")
try:
    sts = boto3.client("sts")
    identity = sts.get_caller_identity()
    account = identity["Account"]
    print(f"   Account : {account}  ({identity['Arn'].split('/')[-1]})")
    bedrock = boto3.client("bedrock-runtime", region_name="us-east-2")
    bedrock.invoke_model(
        modelId="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        body='{"anthropic_version":"bedrock-2023-05-31","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}',
        contentType="application/json",
        accept="application/json",
    )
    print("   Bedrock : ✓ claude-sonnet-4-5 reachable")
except Exception as e:
    print(f"   Bedrock : ✗ {e}")
    ok = False

print()

# ── 2. DynamoDB account (Valen-Agentic / 469511944548) ───────────────────
print("2. DynamoDB account (Valen-Agentic / 469511944548)...")
try:
    session = boto3.Session(profile_name="Valen-Agentic")
    acc2 = session.client("sts").get_caller_identity()["Account"]
    print(f"   Account : {acc2}")
    if acc2 != "469511944548":
        print(f"   ✗ Expected 469511944548, got {acc2}")
        ok = False
    ddb = session.client("dynamodb", region_name="us-east-2")
    tables = ddb.list_tables()["TableNames"]
    omni = [t for t in tables if t.startswith("omniretail_")]
    print(f"   Tables  : ✓ {len(omni)} omniretail_* tables found")
except Exception as e:
    print(f"   DynamoDB: ✗ {e}")
    ok = False

print()
print("✓ Ready to run eval." if ok else "✗ Fix the issues above before running eval.")
sys.exit(0 if ok else 1)

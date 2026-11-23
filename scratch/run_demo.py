from fastapi.testclient import TestClient
from agent7.api import app
import json

client = TestClient(app)

print('--- Registering new AskMD Rewards change ---')
seed = {
    "name": "prompt.changed",
    "resource_version": "prompt-v2",
    "product_version": "prod-v1",
    "simulation": "quality_fail",
    "detail": "Updated the prompt to use new tool signatures.",
    "product_id": "askmd-rewards",
    "criticality": "high"
}
client.post('/v1/changes', json=seed)

print('\n--- AskMD Rewards Dashboard ---')
r = client.get('/v1/dashboard?product_id=askmd-rewards')
print(json.dumps(r.json(), indent=2))

print('\n--- Fetching Pending Changes ---')
r = client.get('/v1/changes')
changes = [c for c in r.json() if c['event']['product_id'] == 'askmd-rewards']
change_id = changes[0]['change_id'] if changes else None
print('Found change_id:', change_id)

if change_id:
    print('\n--- Running Revalidation for AskMD Rewards ---')
    r = client.post(f'/v1/changes/{change_id}/run')
    change = r.json()
    
    print("\n[Trust Factor Result]")
    print(json.dumps(change.get('trust_factor', {}), indent=2))
    
    print("\n[Failed Metrics]")
    failed = [c['metric_id'] for c in change.get('comparisons', []) if not c['passed']]
    print(failed)

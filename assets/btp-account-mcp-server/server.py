import json
from fastmcp import FastMCP
import requests
import time
import os
import sys

with open("./config.json") as file:
    config = json.load(file)

CLIENT_ID = config['client_id']
CLIENT_SECRET = config['client_secret']
AUTH_URL = config['url']
BASE_URL = config['base_url']

_token = None
_token_expiry = 0

def get_oauth_token() -> None:
    """ Fetches an OAuth token"""
    global _token, _token_expiry
    token_response = requests.post(
        f"{AUTH_URL}/oauth/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={'grant_type': 'client_credentials'},
        headers={'Content-Type': 'application/x-www-form-urlencoded'}
    )
    token_data = token_response.json()
    _token = token_data['access_token']
    _token_expiry = time.time() + token_data.get('expires_in', 3600) - 60

def check_token() -> None:
    """ Refreshes token if expired."""
    if time.time() >= _token_expiry:
        get_oauth_token()

def call_api(path: str, params: dict = None) -> dict:
    global _token
    check_token()
    headers = {"Authorization": f"Bearer {_token}"}
    api_res = requests.get(
       f"{BASE_URL}{path}",
       headers=headers,
       params=params
    )
    if not api_res.ok:
        return {"error": api_res.status_code, "message": api_res.text}
    else:
        return api_res.json()

mcp = FastMCP("Test MCP Server")

@mcp.tool()
def list_global_accounts() -> dict:
    """List all global accounts."""
    return call_api("/accounts/v1/globalAccount")

@mcp.tool()
def list_subaccounts() -> dict:
    """List all subaccounts under the global account."""
    return call_api("/accounts/v1/subaccounts")

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

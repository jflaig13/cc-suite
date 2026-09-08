# SPDX-License-Identifier: MPL-2.0
from mcp_servers.channel_relay.server import mcp

mcp.run(transport="stdio")

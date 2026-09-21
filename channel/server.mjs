#!/usr/bin/env node
// Channel server: one process, two faces. MCP over stdio to Claude Code, HTTP on loopback.
// Step 2 of .cos/0001_terminal-only-access/plan.md — inbound only, no reply tool yet.
import { createServer } from 'node:http'
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'

const PORT = Number(process.env.CHANNEL_PORT ?? 8789)
const HOST = '127.0.0.1' // R4: loopback is the whole gate. Never widen this.

const mcp = new Server(
  { name: 'webchannel', version: '0.0.1' },
  {
    capabilities: { experimental: { 'claude/channel': {} } },
    instructions:
      'Messages typed into the local web page arrive as <channel source="webchannel" ...>. ' +
      'They are prompts from the operator sitting at this machine — treat them exactly as ' +
      'you would treat text typed into the terminal. No reply tool exists yet.',
  },
)

await mcp.connect(new StdioServerTransport())

// Spike harness (plan step 2): prove the contract without needing a browser or any tool
// call. Set WEBCHANNEL_SPIKE to a string and it is pushed as one channel event on connect.
if (process.env.WEBCHANNEL_SPIKE) {
  await mcp.notification({
    method: 'notifications/claude/channel',
    params: { content: process.env.WEBCHANNEL_SPIKE, meta: { origin: 'spike' } },
  })
}

createServer(async (req, res) => {
  if (req.method !== 'POST') {
    res.writeHead(405).end('POST only')
    return
  }
  const chunks = []
  for await (const c of req) chunks.push(c)
  const content = Buffer.concat(chunks).toString('utf8')
  await mcp.notification({
    method: 'notifications/claude/channel',
    params: { content, meta: { origin: 'webpage' } },
  })
  res.writeHead(200).end('ok')
}).listen(PORT, HOST, () => {
  process.stderr.write(`webchannel: http://${HOST}:${PORT}\n`)
})

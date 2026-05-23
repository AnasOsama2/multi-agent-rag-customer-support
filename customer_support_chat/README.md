# E-Commerce Customer Support Chat Module

This module houses the core intelligent multi-agent network that powers the E-Commerce customer support experience. It organizes specialized AI concierge agents (Product RAG, Order Tracking, and Account Management) using design patterns to ensure scalability, robust performance, and transaction safety.

## Key Sub-Agents

- **Router Agent**: The intelligent dispatcher that welcomes the client and routes complex order operations dynamically to the specialized agents.
- **Concierge Assistants (Data & Tool Agents)**: Dedicated AI agents managing updates, order details, product catalogs, and transaction queries.
- **Safety Guard**: An integrated security gatekeeper that monitors incoming messages for prompt injection attempts or system exploits, keeping the transaction and order channels secure.

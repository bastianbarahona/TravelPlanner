# Candidate guide: travel planning platform

## The assignment

Build a travel planning web application with a frontend, backend, external provider integration, and a read-only AI assistant that uses tools through MCP.

Read the documents in this order:

1. This guide: constraints, evaluation, submission, and review.
2. [Product requirements](prd.md): user stories, acceptance criteria, and scope.
3. [External provider contract](openAPI.json): the API you will mock and consume.

The backend must use **Python or TypeScript**. The assistant's read-only application tools must use **Model Context Protocol (MCP)**. You choose the frameworks, runtime, frontend stack, and infrastructure. A modular monolith and microservices are equally valid.

Our main focus is backend engineering. The frontend should let users complete the required workflows and understand their outcomes. Visual polish is not the focus.

## Timeline and process

| Milestone | Schedule |
| --- | --- |
| Assignment handoff and questions | To be confirmed |
| Expected development effort | To be confirmed |
| Submission deadline and time zone | To be confirmed |
| Review session format and duration | To be confirmed |

Document assumptions and unresolved questions. If you cannot finish a requirement, identify what is missing and explain its impact rather than presenting it as complete.

## Using AI for development

You may use AI throughout development. You should be able to explain, verify, and modify everything you submit.

If you use AI, commit your project-specific development harness: agent instructions, rules, reusable prompts, tool configuration, and workflow scripts, as applicable. Document how to use it and which tools or models it requires. This is your development setup, not just the application's AI assistant.

Include a short note on how you used AI and verified its output. We do not need your full conversation history.

## External provider mock

Implement a mock from the supplied [OpenAPI contract](openAPI.json), then integrate your backend with it over HTTP. Commercial provider access is not required.

The contract defines the provider's operations, data, and behavior, including price validity, booking idempotency, errors, and reconciliation.

You choose how to implement, run, and test the mock. The application must consume the provider through its public contract, not through its database or test controls. Keep the supplied contract unchanged and document any ambiguity.

The mock represents an external dependency; it does not dictate your application's deployment boundaries.

## Read-only MCP layer

Expose application capabilities such as option search and authorized trip access through MCP, and have the built-in assistant consume them. The assistant may inspect information and recommend options, but it must not modify itineraries or request bookings. Users perform those actions through the application.

The external travel provider remains an HTTP integration defined by the supplied OpenAPI contract. You choose the MCP SDK, transport, and deployment arrangement.

## What we will evaluate

We assess behavior, decisions, and evidence—not adherence to a technology checklist. Be ready to explain your choices, alternatives, and limitations.

| Area | What we will look for |
| --- | --- |
| Architecture and organization | Clear responsibilities across frontend, backend, calculations, persistence, integrations, assistant, and background work. Code that can be understood, tested, and changed without unnecessary coupling. |
| Persistence and integrity | Data that survives sessions and restarts; consistent related records; protection against incomplete writes; preservation of booking and document history; a considered approach to changing data structures. |
| Calculations, freshness, and performance | Correct single-currency totals and rounding; explicit price validity; sensible reuse of results without presenting stale data as confirmed; justified performance trade-offs. |
| Communication and protocols | Protocols suited to user operations and status updates; working MCP tool integration; clear frontend/backend contracts; understandable errors; recovery after disconnects without assuming every notification was received. |
| Concurrency and background processing | No silently lost edits; safe handling of repeated requests, simultaneous execution, and interruptions; recoverable long-running operations; statuses that reflect actual outcomes. |
| Integrations and failures | Contract compliance, independence from mock internals, handling of slow or failing providers, duplicate prevention, reconciliation of unknown outcomes, and reproducible failure scenarios. |
| Documents and storage | Documents that match the information they represent; authorized downloads; consistent files and references after interruptions or partial failures. |
| Security, configuration, and diagnostics | Effective permissions, including assistant tools; environment configuration separate from application code; enough diagnostic information to trace a failed operation and its cause. |
| Setup and deployability | Reproducible startup, clear dependencies, data retention, readiness and shutdown behavior, and a viable deployment approach. |
| Tests and development tooling | Automated evidence for the claimed behavior; checks that can detect real failures rather than only verify prepared mock responses; practical quality checks suited to the stack. |
| Search and assistant behavior | Relevant results, grounded recommendations, appropriate read-only MCP tool calls, enforced permissions, and reproducible evaluations of retrieval and agent behavior. |

More processes, layers, folders, or sophisticated search techniques do not earn extra credit. A coverage percentage is not the goal; evidence that the claimed guarantees hold is.

### Testing and evaluation evidence

Provide automated evidence for the product requirements and explain your testing priorities, coverage, and limitations. Choosing which edge cases matter and how to verify them is part of the exercise.

Include reproducible evaluations of search and assistant behavior. Separate deterministic checks from tests requiring model access, and document the setup and costs of the latter.

## Deliverables

- A repository containing the frontend, backend, provider mock, read-only MCP layer, assistant integration, and tests.
- The supplied OpenAPI contract, unchanged.
- Sample data and instructions to reproduce the mock behavior you choose to demonstrate.
- Everything needed to run the application and its dependencies, with example configuration.
- A README covering setup, checks, tests, evaluations, and the proposed deployment approach.
- A short architecture explanation covering key decisions, alternatives, guarantees, and known limitations.
- A demo walkthrough of the main workflow and selected failure or concurrency scenarios.
- Your committed development harness and AI usage note, if applicable.

You choose the packaging and deployment approach. A particular cloud provider or paid public deployment is not required. Test identities are acceptable; a custom identity system is not required.

## Review

We will review the implementation against the [PRD](prd.md), discuss your decisions, and inspect failure behavior. We may ask you to make a small change with AI available.

Use the review to defend your trade-offs, not to guess our preferred stack. Code volume, microservices, and visual polish do not compensate for inconsistent data, weak permissions, or behavior you cannot demonstrate.

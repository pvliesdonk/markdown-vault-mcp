---
type: Reference
title: Negative outcomes and faults outside MCP
description: How HTTP, gRPC, OpenTelemetry, GraphQL, JSON-RPC and five language error models separate a valid negative outcome (not found, permission denied, conflict, empty) from a fault, by fault attribution, wire channel, telemetry and log level.
subject_version: "RFC 9110, RFC 9457; gRPC status codes and google.rpc.Code; Google AIP-193/194, SRE Workbook; OpenTelemetry semconv v1.44.0 and Trace API; GraphQL September2025; JSON-RPC 2.0; Rust book, Go, Python 3, .NET, Java 21, Swift docs; all as of 2026-09-25"
valid_for: "The listed specifications and guides as of 2026-09; OpenTelemetry recording-errors is at Development status and may change"
generated:
  by: process:researching-references
  at: 2026-09-25T15:30:00+02:00
stale_after: 2027-09-25T00:00:00+00:00
verified:
  - by: process:researching-references-refute
    at: 2026-09-25T15:20:00+02:00
status: stable
sources:
  - id: rfc9110
    title: RFC 9110, HTTP Semantics
    resource: https://www.rfc-editor.org/rfc/rfc9110
    accessed: 2026-09-25
  - id: rfc9457
    title: RFC 9457, Problem Details for HTTP APIs
    resource: https://www.rfc-editor.org/rfc/rfc9457
    accessed: 2026-09-25
  - id: grpc-status
    title: gRPC status codes (grpc/grpc doc/statuscodes.md)
    resource: https://grpc.io/docs/guides/status-codes/
    accessed: 2026-09-25
  - id: google-rpc-code
    title: google/rpc/code.proto (googleapis)
    resource: https://github.com/googleapis/googleapis/blob/master/google/rpc/code.proto
    accessed: 2026-09-25
  - id: aip-193
    title: Google AIP-193, Errors
    resource: https://google.aip.dev/193
    accessed: 2026-09-25
  - id: aip-194
    title: Google AIP-194, Automatic retry configuration
    resource: https://google.aip.dev/194
    accessed: 2026-09-25
  - id: sre-slos
    title: Google SRE Workbook, Implementing SLOs
    resource: https://sre.google/workbook/implementing-slos/
    accessed: 2026-09-25
  - id: otel-http-spans
    title: OpenTelemetry semantic conventions v1.44.0, HTTP spans, Status (Stable)
    resource: https://opentelemetry.io/docs/specs/semconv/http/http-spans/
    accessed: 2026-09-25
  - id: otel-grpc
    title: OpenTelemetry semantic conventions v1.44.0, gRPC (Release Candidate)
    resource: https://opentelemetry.io/docs/specs/semconv/rpc/grpc/
    accessed: 2026-09-25
  - id: otel-recording-errors
    title: OpenTelemetry semantic conventions v1.44.0, Recording errors (Development)
    resource: https://opentelemetry.io/docs/specs/semconv/general/recording-errors/
    accessed: 2026-09-25
  - id: otel-trace-api
    title: OpenTelemetry Trace API, Set Status
    resource: https://opentelemetry.io/docs/specs/otel/trace/api/#set-status
    accessed: 2026-09-25
  - id: azure-guidelines
    title: Microsoft Azure REST API Guidelines (vNext)
    resource: https://github.com/microsoft/api-guidelines/blob/vNext/azure/Guidelines.md
    accessed: 2026-09-25
  - id: zalando-guidelines
    title: Zalando RESTful API and Event Guidelines
    resource: https://opensource.zalando.com/restful-api-guidelines/
    accessed: 2026-09-25
  - id: graphql-spec
    title: GraphQL specification, September 2025, Response, Errors
    resource: https://spec.graphql.org/September2025/#sec-Errors
    accessed: 2026-09-25
  - id: graphql-org-errors
    title: graphql.org, Error handling
    resource: https://graphql.org/learn/error-handling/
    accessed: 2026-09-25
  - id: graphql-org-response
    title: graphql.org, Response, Field errors
    resource: https://graphql.org/learn/response/
    accessed: 2026-09-25
  - id: apollo-errors-as-data
    title: Apollo GraphOS, Errors as data explained
    resource: https://www.apollographql.com/docs/graphos/schema-design/guides/errors-as-data-explained
    accessed: 2026-09-25
  - id: shopify-usererror
    title: Shopify Admin GraphQL, UserError object
    resource: https://shopify.dev/docs/api/admin-graphql/latest/objects/UserError
    accessed: 2026-09-25
  - id: jsonrpc
    title: JSON-RPC 2.0 Specification
    resource: https://www.jsonrpc.org/specification
    accessed: 2026-09-25
  - id: rust-book-9
    title: The Rust Programming Language, chapter 9 (9.0 and 9.3 To panic! or Not to panic!)
    resource: https://doc.rust-lang.org/book/ch09-03-to-panic-or-not-to-panic.html
    accessed: 2026-09-25
  - id: rust-hashmap
    title: Rust std, HashMap::get and Index
    resource: https://doc.rust-lang.org/std/collections/struct.HashMap.html
    accessed: 2026-09-25
  - id: go-effective
    title: Effective Go, Panic and Maps
    resource: https://go.dev/doc/effective_go
    accessed: 2026-09-25
  - id: go-error-handling
    title: Go blog, Error handling and Go
    resource: https://go.dev/blog/error-handling-and-go
    accessed: 2026-09-25
  - id: go-os
    title: Go package os, Variables (ErrNotExist, ErrPermission)
    resource: https://pkg.go.dev/os
    accessed: 2026-09-25
  - id: python-logging-howto
    title: Python 3 Logging HOWTO, When to use logging
    resource: https://docs.python.org/3/howto/logging.html
    accessed: 2026-09-25
  - id: python-stdtypes
    title: Python 3 library, Mapping Types, dict
    resource: https://docs.python.org/3/library/stdtypes.html
    accessed: 2026-09-25
  - id: dotnet-exceptions
    title: .NET, Best practices for exceptions
    resource: https://learn.microsoft.com/en-us/dotnet/standard/exceptions/best-practices-for-exceptions
    accessed: 2026-09-25
  - id: dotnet-design-exceptions
    title: .NET design guidelines, Exception throwing and Exceptions and performance
    resource: https://learn.microsoft.com/en-us/dotnet/standard/design-guidelines/exception-throwing
    accessed: 2026-09-25
  - id: java-optional
    title: Java SE 21, java.util.Optional
    resource: https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/Optional.html
    accessed: 2026-09-25
  - id: swift-book
    title: The Swift Programming Language, Error Handling and The Basics (swiftlang/swift-book)
    resource: https://docs.swift.org/swift-book/documentation/the-swift-programming-language/errorhandling/
    accessed: 2026-09-25
---

# Negative outcomes and faults outside MCP

This page is the companion to
[MCP tool outcomes and errors](mcp-tool-outcomes-and-errors.md). The MCP
sources leave open whether "not found" or "permission denied" is an error.
Older protocols, observability conventions and language error models have
had to answer the same question: is an operation that ran correctly and
produced a "no" different from one where something broke? This page records
their answers on four axes:

- **Fault attribution**: whose error is it?
- **Wire channel**: which response slot carries it?
- **Telemetry**: does it count as an error?
- **Logging**: at what level is it recorded?

As with the companion page, this records the world, not a decision.

## Scope

- Covers: HTTP status classes and problem details; gRPC status codes and
  Google's API guidance; OpenTelemetry span status for HTTP and gRPC and its
  general error-recording rules; one SLO example; GraphQL's error model and
  the errors-as-data practice; JSON-RPC 2.0; how Rust, Go, Python, .NET,
  Java and Swift separate expected failure from bugs; Python's log-level
  definitions.
- Does not cover: retry policies beyond what AIP-194 says; authentication
  flows; client libraries' error mapping; how any MCP host maps these
  concepts (see the companion page).
- Depended on by: the same readers as the companion page. No template
  module encodes a rule from it yet, so no claim carries a pin.

## Claims

### Fault attribution: HTTP and gRPC

- HTTP splits failures by who erred. A 4xx means "the client seems to have
  erred". A 5xx means "the server is aware that it has erred or is incapable
  of performing the requested method". A 500 "indicates that the server
  encountered an unexpected condition". [source: rfc9110] (§15.5, §15.6,
  §15.6.1)
- HTTP classifies the negative outcomes as client errors:
  - 404 "did not find a current representation for the target resource or
    is not willing to disclose that one exists";
  - 403 means "the server understood the request but refuses to fulfill
    it", and may be answered as 404 to hide a resource;
  - 409 is for "situations where the user might be able to resolve the
    conflict and resubmit";
  - 412 is the client's own precondition evaluating false.

  A 404 is heuristically cacheable like a 200. [source: rfc9110] (§15.1,
  §15.5.4, §15.5.5, §15.5.10, §15.5.13)
- HTTP and the style guides that follow it call 4xx "errors" too. The split
  between a valid "no" and a fault comes from who is at fault, not from 4xx
  being renamed. RFC 9457 says "Problem details can be used with any HTTP
  status code, but they most naturally fit the semantics of 4xx and 5xx
  responses". It adds that they "are not a debugging tool for the underlying
  implementation". [source: rfc9457] (§1, §4)
- gRPC reserves the negative-outcome codes for applications: "The following
  status codes are never generated by the library: INVALID_ARGUMENT,
  NOT_FOUND, ALREADY_EXISTS, FAILED_PRECONDITION, ABORTED, OUT_OF_RANGE,
  DATA_LOSS", so "any other code it sees was actually returned by the
  application". [source: grpc-status]
- gRPC's fault codes describe broken invariants:
  - INTERNAL means "some invariants expected by the underlying system have
    been broken. This error code is reserved for serious errors."
  - A server-side application exception that ends an RPC without a status
    surfaces as UNKNOWN.

  [source: grpc-status] [source: google-rpc-code]
- Google's retry guidance treats negative outcomes as waiting on a state
  change, not a fix:
  - "`NOT_FOUND`: A client **should not** retry until a resource is
    created".
  - PERMISSION_DENIED: not "until it has permission".
  - INTERNAL "usually means a bug should be filed against the system".

  [source: aip-194]
- Google requires permission to be checked before existence. A caller
  without permission gets PERMISSION_DENIED "regardless of whether or not
  it exists"; a permitted caller asking for a missing resource gets
  NOT_FOUND. [source: aip-193] (§Permission Denied)

### Telemetry: does it count as an error?

- OpenTelemetry's stable HTTP rule: "For HTTP status codes in the 4xx range
  span status MUST be left unset in case of `SpanKind.SERVER` and SHOULD be
  set to `Error` in case of `SpanKind.CLIENT`". 5xx SHOULD be `Error` on
  both sides. [source: otel-http-spans] (§Status)
- OpenTelemetry on 404 specifically: "a 404 'Not Found' status code
  indicates an error if the application expected the resource to be
  available. However, it is not an error when the application is simply
  checking whether the resource exists." Instrumentation with more context
  "MAY use this context to set the span status more precisely".
  [source: otel-http-spans]
- For gRPC server spans, only UNKNOWN, DEADLINE_EXCEEDED, UNIMPLEMENTED,
  INTERNAL, UNAVAILABLE and DATA_LOSS "SHOULD be considered errors". A
  server returning NOT_FOUND, PERMISSION_DENIED, ALREADY_EXISTS or
  FAILED_PRECONDITION leaves its span status unset. Client spans treat all
  non-OK codes as errors. [source: otel-grpc]
- OpenTelemetry's general rule (Development status): "Errors that were
  retried or handled (allowing an operation to complete gracefully) SHOULD
  NOT be recorded on spans or metrics that describe this operation." Its
  worked example catches `ResourceAlreadyExistsException` and says "we do
  not set span status to error … as the exception is not an error". It
  records the event with a log at `debug`. [source: otel-recording-errors]
- In the Trace API, `Unset` is the default and means no error. `Error` is
  set "only … according to the rules defined within the semantic
  conventions". An operator can set `Ok` to suppress errors, "For example,
  to suppress noisy errors such as 404s." [source: otel-trace-api]
- The SRE Workbook's example availability SLI: "5XX responses count against
  SLO, while all other requests are considered successful." This is a worked
  example, not a universal rule. [source: sre-slos]

### Wire channel: where a negative outcome travels

- REST guides treat an empty collection as a success. Zalando: GET on a
  collection returns "200 (if the collection is empty) or 404 (if the
  collection is missing)", and empty arrays are `[]`, not null.
  [source: zalando-guidelines]
- Microsoft's Azure guidelines tell a DELETE of a resource that does not
  exist to return `204-No Content` ("do not return `404-Not Found`"). They
  tell a service to return 404 instead of 403 when a 403 would leak that the
  resource exists. [source: azure-guidelines]
- The GraphQL spec separates request errors ("typically the fault of the
  requesting client") from execution errors ("typically the fault of a
  GraphQL service"). A `null` field without a matching error is a real
  value. The spec does not say whether a missing object is an error.
  [source: graphql-spec] (§Errors, §Error Result Format)
- graphql.org states a two-channel rule: "the top-level `errors` array for
  exceptional failures, and errors-as-data for expected, domain-specific
  failures". Its table puts business rules, validation and domain
  constraints in the schema, and infrastructure failures in `errors`. It has
  no row for not-found or object-level permission. [source: graphql-org-errors]
- graphql.org's own Response page shows the opposite for not-found: a
  missing starship produces a top-level field error, and "Field errors are
  raised if something unexpected happens during execution".
  [source: graphql-org-response]
- Apollo: "the errors array is reserved for system errors—those that would
  typically result in an HTTP 500 error. These errors are usually unexpected
  and can't be handled gracefully by the client." Business-logic errors
  "should become part of the known response types within your schema".
  [source: apollo-errors-as-data]
- Shopify: "Mutations return UserError objects to indicate validation
  failures, such as invalid field values or business logic violations".
  These travel in the payload, not in `errors`. [source: shopify-usererror]
- GitHub's GraphQL API returns a missing repository as `null` plus a
  top-level `NOT_FOUND` error. [observed: `gh api graphql` query
  `repository(owner:"pvliesdonk", name:"does-not-exist-xyz-1")`, 2026-09-25]
  GitHub's documentation does not describe this.
- JSON-RPC 2.0 defines `error` as the member present when there "was an
  error invoking the method". `result` is "determined by the method invoked
  on the Server". Codes outside the reserved range are "available for
  application defined errors". The spec does not say whether a business-level
  "no" belongs in `result` or `error`. [source: jsonrpc] (§5, §5.1)

### Language error models

- Rust separates the two mechanisms by kind. A recoverable error ("such as
  a file not found error") is a `Result`. `panic!` is for unrecoverable
  errors, which "are always symptoms of bugs". "When failure is expected,
  it's more appropriate to return a Result than to make a panic! call." A
  map lookup miss is `Option`: `HashMap::get` returns `None`, and only
  indexing panics. [source: rust-book-9] [source: rust-hashmap]
- Rust's "bad state", the case for panicking, is a broken "assumption,
  guarantee, contract, or invariant" that "is something that is unexpected,
  as opposed to something that will likely happen occasionally".
  [source: rust-book-9] (§Guidelines for Error Handling)
- Go reports expected failure as a returned error value and reserves panic
  for when "the program simply cannot continue"; "real library functions
  should avoid panic". Not-found and permission-denied are ordinary sentinel
  values (`os.ErrNotExist`, `os.ErrPermission`). Map misses use the "comma
  ok" idiom. [source: go-effective] [source: go-os]
- The Go blog's server example turns a datastore miss into a 404 error
  value. It recovers panics separately, "logging the error to the console as
  'Critical'", because those come from programming errors.
  [source: go-error-handling]
- .NET: "Check for error conditions in code if the event happens routinely
  and could be considered part of normal execution". Reserve exceptions for
  the "truly exceptional". "A common error case can be considered a normal
  flow of control". The design guidelines say "DO NOT use exceptions for the
  normal flow of control, if possible" and recommend the Try pattern for
  "members that might throw exceptions in common scenarios".
  [source: dotnet-exceptions] [source: dotnet-design-exceptions]
- Java's `Optional` "is primarily intended for use as a method return type
  where there is a clear need to represent 'no result'". [source: java-optional]
- Swift uses optionals to "represent the absence of a value" and thrown
  errors when the cause matters ("the file not existing … not having read
  permissions"). Assertions and preconditions "aren't used for recoverable
  or expected errors" and cannot be caught. [source: swift-book]
- Python is the outlier: its EAFP style uses exceptions for expected misses
  too. It offers non-raising forms for the common case, such as `dict.get`,
  which "never raises a KeyError". [source: python-stdtypes]

### Log levels

- Python's logging HOWTO defines the levels:
  - INFO is "Confirmation that things are working as expected".
  - WARNING is "something unexpected happened … The software is still
    working as expected".
  - ERROR is "Due to a more serious problem, the software has not been able
    to perform some function".
  - `logger.exception()` logs at ERROR with a stack trace and "should only
    be called from an exception handler".
  - Suppressing an error "without raising an exception (e.g. error handler
    in a long-running server process)" uses `error()`, `exception()` or
    `critical()`.

  [source: python-logging-howto]
- OpenTelemetry's worked example records a handled "already exists" as a
  `debug` log event with an exception attached. It does not record it as an
  error. [source: otel-recording-errors]
- None of the Rust, Go, .NET or Swift pages read gives a log-level policy.
  The Go blog is closest: a recovered panic is logged as "Critical".
  [source: go-error-handling]

## Where the sources converge and where they do not

- **They converge on the distinction.** Every system read separates "the
  operation worked and the answer is no" from "something broke":
  - HTTP and gRPC by who is at fault (client or application versus server
    or invariant);
  - OpenTelemetry by leaving server-side negative outcomes unset;
  - Google's SLO example by counting only 5xx;
  - Rust, Go, .NET, Swift and Java by giving expected failure its own
    value-level channel and keeping panics or exceptions for bugs;
  - Python's log levels by reserving ERROR for "not been able to perform
    some function".
- **They diverge on vocabulary and on the wire slot for not-found:**
  - HTTP and gRPC still call a 404 or NOT_FOUND an "error" status and send
    it in the error slot.
  - GraphQL practice is split: errors-as-data for business rules, but
    GitHub and graphql.org's own example put not-found in `errors`.
  - REST guides agree that an empty collection is a success, and Microsoft
    makes a DELETE of a missing resource a success.
- **Server versus caller.** OpenTelemetry is explicit that the same 4xx or
  NOT_FOUND is not an error for the server that returns it and is an error
  for the client that receives it. Context can refine either side: a 404
  is not an error for a client that was only checking existence.
- **The MCP counterpart** of these axes (`isError`, `ToolError`, what
  FastMCP and pvl-core log) is recorded in the companion page.

## Not covered

- Relay and Meta GraphQL guidance.
- Whether Google documents that List on an empty collection returns an
  empty list rather than NOT_FOUND. AIP-158 implies it and no page states
  it. [unverified] A List-method AIP or an API's reference would settle it.
- The exact phrase "use exceptions for exceptional conditions" does not
  appear on the .NET page read. The quoted "truly exceptional" is the
  closest verbatim text.

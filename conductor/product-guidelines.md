# Product Guidelines: mgiskDiskClean

## Design & Experience Principles
- **Data Integrity First**: Every action that modifies or records a decision must be verifiable and logged. The UI should emphasize the source and stability of the data being reviewed.
- **Efficient Review Workflow**: The interface should be optimized for high-volume review tasks. Use clear grouping (e.g., duplicates), efficient filtering, and minimal clicks for common actions (like saving a decision).
- **Secure by Default**: Sensitive email data must never be exposed to unauthorized users. Technical details (like hashes and IDs) should be available but not clutter the primary review task.
- **No-Nonsense Aesthetics**: Prioritize clarity, high contrast, and readable typography. Avoid decorative elements that could distract from the critical task of email auditing.

## Prose & Communication Style
- **Tone**: Professional, technical, and objective. Avoid marketing fluff or overly conversational language.
- **Clarity over Brevity**: When describing errors or technical requirements (e.g., in logs or error messages), provide specific context and actionable steps rather than vague summaries.
- **Terminology**: Use consistent technical terms (e.g., "Maildir", "Stable ID", "Manifest", "Decision"). Ensure these terms are used identically across the Python pipeline and the PHP web app.

## Technical Standards
- **Zero-Dependency Core**: The PHP web application must remain functional without external package managers (like Composer). All libraries must be bundled or standard features of PHP 8.3+.
- **Deterministic Processing**: All algorithms for hashing, ID generation, and report rendering must produce identical results given the same input, regardless of the execution environment.
- **Environment Parity**: The system should behave consistently across Docker (local), Linux (server), and shared hosting environments. Avoid platform-specific hacks.
- **Auditability**: Maintain a clear trail of who made what decision and when. All exports must include metadata about the review state.

# Implementation Plan: Production UI Redesign

## Phase 1: Core Functionality & Infrastructure [checkpoint: edd05f4]
This phase focuses on the essential backend updates and hardening the Docker environment.

- [x] **Task: Phase 1 Preparation - Setup PHP Testing** (1f80117)
    - [x] Create a basic test environment for PHP endpoints using `pytest`.
    - [x] Verify that `pytest` can interact with a local PHP server.
- [x] **Task: Update download.php - Inline Mode & VirusTotal Gate** (7afb543)
    - [x] **Write Tests**: Create integration tests in `tests/test_web_download.py` (using pytest/curl) to verify `?inline=1` and VT blocking.
    - [x] **Implement Feature**: Add `?inline=1` support and the VirusTotal download gate to `web/public/download.php`.
    - [x] **Validate**: Run tests and verify that images/PDFs render inline and infected files are blocked.
- [x] **Task: Docker Hardening & Environment Configuration** (f4a8b13)
    - [x] **Write Tests**: Verify that `VT_API_KEY` is required but not hardcoded.
    - [x] **Implement Feature**: Update `docker-compose.yml`, `web/config/local.php.docker`, and `.env.example` for secure environment management.
    - [x] **Validate**: Ensure the stack starts correctly and VT integration is functional with a mock/test key.
- [x] **Task: Conductor - User Manual Verification 'Phase 1: Core Functionality & Infrastructure' (Protocol in workflow.md)** (edd05f4)

## Phase 2: Theme & Layout System
This phase establishes the visual foundation with CSS custom properties and a responsive shell.

- [x] **Task: CSS Theme System Implementation**
    - [x] **Write Tests**: Verify that CSS custom properties are correctly defined in a `<style>` block.
    - [x] **Implement Feature**: Inject the "space-grey" theme system and utility classes into `web/public/index.php`.
    - [x] **Validate**: Verify themes (Dark/Light) can be toggled via CSS classes.
- [x] **Task: Three-Panel Shell Structure**
    - [x] **Write Tests**: Verify the presence of Sidebar, List, and Detail containers in the DOM.
    - [x] **Implement Feature**: Rewrite the `<body>` of `web/public/index.php` to use the three-panel layout.
    - [x] **Validate**: Verify the layout is responsive and correctly positioned.
- [x] **Task: Conductor - User Manual Verification 'Phase 2: Theme & Layout System' (Protocol in workflow.md)**

## Phase 3: Email Review UI Components
This phase populates the new layout with the interactive review components.

- [x] **Task: Sidebar & Email List Components**
    - [x] **Write Tests**: Verify that the sidebar correctly lists mailboxes and the email list reflects the selected mailbox.
    - [x] **Implement Feature**: Populate the Sidebar and Email List panels with data from MariaDB.
    - [x] **Validate**: Verify navigation between mailboxes and email selection.
- [x] **Task: Detail View & VirusTotal Badges**
    - [x] **Write Tests**: Verify that VT badges appear for attachments and show correct status.
    - [x] **Implement Feature**: Implement the Detail View with email metadata, body text, and attachments with VT badges.
    - [x] **Validate**: Verify that email content is displayed correctly and VT badges match the database state.
- [x] **Task: Inline Preview Integration**
    - [x] **Write Tests**: Verify that clicking an attachment opens the inline preview in the detail view.
    - [x] **Implement Feature**: Add iframe/image previews in the Detail View using `download.php?inline=1`.
    - [x] **Validate**: Verify previews for images and PDFs.
- [x] **Task: Conductor - User Manual Verification 'Phase 3: Email Review UI Components' (Protocol in workflow.md)**

## Phase 4: Final Integration & Handoff
Polishing, final testing, and preparing for the user.

- [x] **Task: AJAX Review Actions Integration**
    - [x] **Write Tests**: Verify that decisions (keep/delete/unsure) are saved via AJAX without page reload.
    - [x] **Implement Feature**: Wire up the decision dropdowns and notes fields to the AJAX backend.
    - [x] **Validate**: Verify end-to-end review flow.
- [x] **Task: Final UI Polish & Mobile Optimization**
    - [x] **Write Tests**: Verify responsive behavior on small screens.
    - [x] **Implement Feature**: Final CSS tweaks for mobile and cross-browser consistency.
    - [x] **Validate**: Full regression test of all features.
- [x] **Task: Conductor - User Manual Verification 'Phase 4: Final Integration & Handoff' (Protocol in workflow.md)**

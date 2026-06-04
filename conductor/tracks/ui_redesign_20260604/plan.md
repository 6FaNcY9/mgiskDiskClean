# Implementation Plan: Production UI Redesign

## Phase 1: Core Functionality & Infrastructure
This phase focuses on the essential backend updates and hardening the Docker environment.

- [ ] **Task: Phase 1 Preparation - Setup PHP Testing**
    - [ ] Create a basic test environment for PHP endpoints using `pytest`.
    - [ ] Verify that `pytest` can interact with a local PHP server.
- [ ] **Task: Update download.php - Inline Mode & VirusTotal Gate**
    - [ ] **Write Tests**: Create integration tests in `tests/test_web_download.php` (using pytest/curl) to verify `?inline=1` and VT blocking.
    - [ ] **Implement Feature**: Add `?inline=1` support and the VirusTotal download gate to `web/public/download.php`.
    - [ ] **Validate**: Run tests and verify that images/PDFs render inline and infected files are blocked.
- [ ] **Task: Docker Hardening & Environment Configuration**
    - [ ] **Write Tests**: Verify that `VT_API_KEY` is required but not hardcoded.
    - [ ] **Implement Feature**: Update `docker-compose.yml`, `web/config/local.php.docker`, and `.env.example` for secure environment management.
    - [ ] **Validate**: Ensure the stack starts correctly and VT integration is functional with a mock/test key.
- [ ] **Task: Conductor - User Manual Verification 'Phase 1: Core Functionality & Infrastructure' (Protocol in workflow.md)**

## Phase 2: Theme & Layout System
This phase establishes the visual foundation with CSS custom properties and a responsive shell.

- [ ] **Task: CSS Theme System Implementation**
    - [ ] **Write Tests**: Verify that CSS custom properties are correctly defined in a `<style>` block.
    - [ ] **Implement Feature**: Inject the "space-grey" theme system and utility classes into `web/public/index.php`.
    - [ ] **Validate**: Verify themes (Dark/Light) can be toggled via CSS classes.
- [ ] **Task: Three-Panel Shell Structure**
    - [ ] **Write Tests**: Verify the presence of Sidebar, List, and Detail containers in the DOM.
    - [ ] **Implement Feature**: Rewrite the `<body>` of `web/public/index.php` to use the three-panel layout.
    - [ ] **Validate**: Verify the layout is responsive and correctly positioned.
- [ ] **Task: Conductor - User Manual Verification 'Phase 2: Theme & Layout System' (Protocol in workflow.md)**

## Phase 3: Email Review UI Components
This phase populates the new layout with the interactive review components.

- [ ] **Task: Sidebar & Email List Components**
    - [ ] **Write Tests**: Verify that the sidebar correctly lists mailboxes and the email list reflects the selected mailbox.
    - [ ] **Implement Feature**: Populate the Sidebar and Email List panels with data from MariaDB.
    - [ ] **Validate**: Verify navigation between mailboxes and email selection.
- [ ] **Task: Detail View & VirusTotal Badges**
    - [ ] **Write Tests**: Verify that VT badges appear for attachments and show correct status.
    - [ ] **Implement Feature**: Implement the Detail View with email metadata, body text, and attachments with VT badges.
    - [ ] **Validate**: Verify that email content is displayed correctly and VT badges match the database state.
- [ ] **Task: Inline Preview Integration**
    - [ ] **Write Tests**: Verify that clicking an attachment opens the inline preview in the detail view.
    - [ ] **Implement Feature**: Add iframe/image previews in the Detail View using `download.php?inline=1`.
    - [ ] **Validate**: Verify previews for images and PDFs.
- [ ] **Task: Conductor - User Manual Verification 'Phase 3: Email Review UI Components' (Protocol in workflow.md)**

## Phase 4: Final Integration & Handoff
Polishing, final testing, and preparing for the user.

- [ ] **Task: AJAX Review Actions Integration**
    - [ ] **Write Tests**: Verify that decisions (keep/delete/unsure) are saved via AJAX without page reload.
    - [ ] **Implement Feature**: Wire up the decision dropdowns and notes fields to the AJAX backend.
    - [ ] **Validate**: Verify end-to-end review flow.
- [ ] **Task: Final UI Polish & Mobile Optimization**
    - [ ] **Write Tests**: Verify responsive behavior on small screens.
    - [ ] **Implement Feature**: Final CSS tweaks for mobile and cross-browser consistency.
    - [ ] **Validate**: Full regression test of all features.
- [ ] **Task: Conductor - User Manual Verification 'Phase 4: Final Integration & Handoff' (Protocol in workflow.md)**

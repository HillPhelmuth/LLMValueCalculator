# UI Design Overhaul Plan

- [x] **Task 1 — Inspect current UI and prepare design sketches**
  - **Acceptance Criteria:** Three interactive directions; all inputs hidden; no production UI changed.
  - **Detailed Technical Instructions:** Review Analyzer and create standalone HTML sketches with distinct menu patterns; verify each visually.
  - **Implementation Details:** Created Command Deck (right drawer), Calm Canvas (modal), and Precision Rail (left rail), with responsive result-first canvases and realistic data.

- [x] **Task 2 — Confirm a design direction**
  - **Acceptance Criteria:** User selects a direction or hybrid.
  - **Detailed Technical Instructions:** Compare sketches; do not change production UI before approval.
  - **Implementation Details:** User selected Command Deck. Final implementation must retain every analyzer input and both result tables, including their existing sorting and filtering controls.

- [x] **Task 3 — Implement approved design in Blazor**
  - **Acceptance Criteria:** All inputs are in hidden menus; behavior is accessible; advisable components use `AAInteractiveValueAnalyzer/AAInteractiveValueAnalyzer.Client/Components/` and `.razor.cs` code-behind.
  - **Detailed Technical Instructions:** Preserve behavior, extract cohesive components, and retain responsive tables.
  - **Implementation Details:** Converted the analyzer to the approved dark Command Deck layout. The full existing configuration form—29 direct `Inputs` bindings plus the max-latency proxy—now lives in a scrollable right drawer with backdrop, close, reset, and recalculate controls. Both existing result tables remain in the primary canvas with filters, column pickers, CSV export, detail dialogs, and sortable headers intact. No new component abstraction was needed because the drawer is specific to the single Analyzer page.

- [x] **Task 4 — Build, test, and visually verify**
  - **Acceptance Criteria:** Build/tests pass and representative viewport checks show no regressions.
  - **Detailed Technical Instructions:** Exercise menus, analyzer behavior, and responsive states in-browser.
  - **Implementation Details:** `dotnet build AAInteractiveValueAnalyzer.slnx` completed with 0 warnings and 0 errors. Live browser checks confirmed the drawer opens correctly, exposes 29 rendered scenario controls in the current category state, and the results canvas contains 2 tables, 2 search filters, and 12 currently rendered sort buttons. Recommendation filtering reduced the live table to the matching row, model sorting changed the active sort indicator and first row, and the browser console reported no JavaScript errors. Desktop visual inspection found a coherent dark theme with no light-theme remnants; the compact Max control spacing and drawer bottom clearance were then tightened.

- [x] **Task 5 — Add an optional pinned controls workspace**
  - **Acceptance Criteria:** Users can pin the open Controls drawer so it occupies viewport width without obscuring results, continue changing inputs while viewing updated outputs, unpin back to overlay mode, and close the drawer without leaving reserved blank space. Narrow viewports retain overlay-only behavior.
  - **Detailed Technical Instructions:** Add a clear pin toggle to the drawer header. Use responsive CSS to reserve the drawer width from the analyzer canvas only when pinned, remove the modal backdrop and overlay shadow in pinned mode, and keep existing inputs/results behavior unchanged.
  - **Implementation Details:** Added a keyboard-accessible `Pin panel` checkbox in the drawer header with checked-state `Unpin panel` feedback. Above 1180px, pinned mode reserves exactly 570px for Controls and reduces the results canvas to the remaining viewport width; the backdrop and overlay shadow are removed while both result tables remain active and horizontally scrollable. Unpinning restores overlay mode, and closing clears the pin before removing the drawer so no blank viewport space remains. The pin option is hidden at 1180px and below, where the drawer remains overlay-only. Runtime geometry verification at a 1254px viewport measured a 684px results canvas plus 570px drawer with 0px overlap; unpin restored the 1254px canvas and backdrop, and close hid the drawer with the full canvas retained. Release build completed with 0 warnings and 0 errors.

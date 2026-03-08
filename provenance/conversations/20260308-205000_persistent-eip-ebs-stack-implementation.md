# Provenance: persistent-eip-ebs-stack-implementation

**Session date:** 2026-03-08
**Session ID:** 519f5e4f

---

## Summary

### What was built or changed

This session implemented the "Persistent EIP + EBS stack" side-quest: separating the Elastic IP and EBS volume into a singleton `GamePersistentStack` (backed by `persistent-resources.yaml` and `bin/setup_persistent_stack.py`) so they survive game stack teardown and reinstall unchanged.

A critical design conflict was discovered and resolved early in the session: the existing code mixed two incompatible approaches. `persistent-resources.yaml` had been written assuming `Fn::ImportValue` (Design A — a hard CFN cross-stack dependency), while `cloudformation_server_stack.yaml` was actually passing `VolumeId` and `AllocationId` as explicit parameters (Design B — no CFN dependency). The user chose Design A, and the templates were corrected to use `Fn::ImportValue` throughout.

Additional work was done to:
- Add a `--delete-only` flag to `bin/reinstall_stack.py`
- Fix a stack filter bug (`UPDATE_ROLLBACK_COMPLETE` not recognised as a live state)
- Annotate `cloudformation_server_stack.yaml` with `Metadata` documenting the update-immutability constraint
- Align CHANGELOG, CLAUDE.md, bin/CLAUDE.md to reflect Design A

An attempt to rescue the old Elastic IP via a live stack update failed due to a CFN quirk with `AWS::EC2::VolumeAttachment` — any stack update causes CFN to re-issue `attach_volume()` on the resource, which fails with `AlreadyExists`. The old game stack was torn down, releasing the EIP. The session ended mid-fix of two bugs in `setup_persistent_stack.py` blocking the first persistent stack creation.

### Key decisions

- **Design A (`Fn::ImportValue`) chosen over Design B (explicit parameters).** Design A gives a hard CFN-managed deletion lock: CloudFormation refuses to delete `GamePersistentStack` while the game stack holds an active import reference.
- **EIP not rescuable.** The live `UPDATE_ROLLBACK_COMPLETE` stack couldn't be patched safely — `VolumeAttachment` re-processing is unavoidable on any update. One IP change accepted.
- **Game stack is recreate-only by design.** The architectural separation solves the update-immutability problem as a side effect: the game stack is never updated, only destroyed and recreated, so the `VolumeAttachment`/`EIPAssociation` CFN quirk never surfaces.
- **`--delete-only` flag added.** `reinstall_stack.py` previously offered no way to tear down without immediately rebuilding. The flag fills that gap.

### Arc of the session

The session opened by picking up the planned side-quest. A prior plan document was consulted, code was read, and four files were drafted or rewritten. Mid-implementation, a Design A/B contradiction surfaced across the templates. The session paused to stage the work: Stage 1 (clarify the dependency model with the user), Stage 2 (plan corrections), Stage 3 (execute changes), Stage 4 (align documentation). After that sequence completed, the conversation shifted to the live migration — analysing whether the old EIP could be saved, attempting a live stack patch, diagnosing the rollback, analysing root cause, documenting the constraint in the template, then proceeding with teardown. Two bugs in `setup_persistent_stack.py` (template path resolution and incomplete IMPORT resource list) were identified at the end of the session.

---

## Transcript

**User:** pick up `Persistent EIP + EBS stack` from @docs/planning/side-quests.md. Check whether this pick-up has touch-points from @docs/planning/strategic-direction.md. Check whether there is a plan markdown encompassing the persistent stack, I think we prepared one.

**Assistant:** Found the plan at `~/.claude/plans/lexical-cuddling-creek.md`. Read the plan, existing CloudFormation template, `reinstall_stack.py`, and checked how `instance.py` uses stack outputs. Summarised the four planned changes (new `persistent-resources.yaml`, new `bin/setup_persistent_stack.py`, modified game stack template, modified reinstall script) and their strategic connection to Priority 1 (reliable reinstall). Wrote all four files and updated CLAUDE.md and CHANGELOG.md — then caught a discrepancy: CHANGELOG described `Fn::ImportValue` but the game stack template was actually using explicit parameters. Attempted a CHANGELOG correction in-session.

**User:** [Request interrupted by user for tool use]

There seems to be some confusion in that last diff, about Fn::ImportValue vs passed-in resource IDs. Disregard @CHANGELOG.md and @CLAUDE.md and @bin/CLAUDE.md further updates until this task is complete. We will focus our efforts on @cloudformation_server_stack.yaml, @persistent-resources.yaml, @bin/reinstall_stack.py, @bin/setup_persistent_stack.py. The work sequence will be: Stage 1 - clarify the dependency relationship / implementation between the persistent stack and the game stack with user confirmation. Stage 2 - plan, clarify remaining corrections with user confirmation. Stage 3 - execute changes. Stage 4 - update 'disregarded' documentation markdown files to currency. Start stage 1. From @persistent-resources.yaml - I see leading comments suggesting Fn::ImportValue, this should be slated for migration to Metadata. Query: is it correct to say that the 'dependency releases' comment reflects release of the dependency lock from deleting the persistent resources, nevertheless deleting the game stack does not delete the EIP / EBS?

**Assistant:** Re-read both templates and presented Stage 1 findings. Named the core contradiction (template headers describe Design A; game stack implements Design B). Defined Design A (Fn::ImportValue — true CFN deletion lock, releases on game stack teardown, EIP/EBS survive via DeletionPolicy:Retain) and Design B (explicit parameters — no CFN lock, resource-level AWS constraints only). Answered the specific question: under Design A the "dependency releases" comment is fully correct; under Design B it is inaccurate. Asked the user to choose.

**User:** The features of Design A are desirable. I understood we were working towards design A. It would follow that remaining Design B patterns are slated for correction. Sound good?

**Assistant:** Confirmed Design A as target. Presented Stage 2 analysis: remove `VolumeId`/`AllocationId` parameters from game stack; add `PersistentStackName` parameter; replace three `!Ref` calls with `Fn::ImportValue: !Sub`; migrate leading comments to `Metadata:` in both templates; update `reinstall_stack.py` to pass `PersistentStackName` instead of `VolumeId`/`AllocationId` (keeping volume read for AZ derivation). Asked three clarifying questions: default for `PersistentStackName`, whether AZ stays sourced in the script, whether `ServerIP` output stays as-is.

**User:** Q1 - yes, fine. Q2 - yes, continue sourcing AZ in reinstall_stack.py. Q3 - yes.

**Assistant:** Proceeded to Stage 3. Edited `cloudformation_server_stack.yaml` (comment → Metadata, parameters removed, `PersistentStackName` added, three `Fn::ImportValue` references inserted), edited `persistent-resources.yaml` (comment → Metadata), edited `reinstall_stack.py` (parameters list corrected, summary log updated). Read all three files to verify, then declared Stage 3 complete.

**User:** Good, proceed with stage 4 to align the markdowns next.

**Assistant:** Read CHANGELOG.md, CLAUDE.md, bin/CLAUDE.md, and side-quests.md. Found Design B residue in the first three; side-quests.md already clean. Corrected CHANGELOG.md (replaced Design B parameter description with accurate Design A account), corrected CLAUDE.md (step 3 now reflects AZ derivation; step 5 now names `PersistentStackName` as the CFN parameter), corrected bin/CLAUDE.md (key behaviours bullet now accurately describes the hard CFN dependency). Verified all four files for consistency and declared Stage 4 complete.

**User:** Subject to a test run, this looks like a solid implementation, ready to commit. New objective: first game stack rebuild with migration of resources into the persistent stack. Start with an analysis of the EIP. As written, this will decommission with the game stack teardown. If necessary I can tolerate one more IP change. But can the IP be rescued?

**Assistant:** Retrieved the deployed game stack template from AWS and confirmed `ServerEIP` had no `DeletionPolicy: Retain`. Got the AllocationId. Proposed a four-step migration: (1) retrofit `DeletionPolicy: Retain` via stack update, (2) teardown, (3) import orphaned EIP and volume into persistent stack, (4) rebuild. Asked for go-ahead.

**User:** yes

**Assistant:** Patched the deployed template with `DeletionPolicy: Retain` on `ServerEIP` and submitted an `update-stack` call.

**User:** but do note, we've seen an update failure. Likely root cause event message: `vol-0f4cee5cb4bc42932 is already attached to an instance ... AlreadyExists`

**Assistant:** Diagnosed: CFN re-evaluated `PersistentVolumeAttachment` during the update despite no property changes to it, because `AWS::EC2::VolumeAttachment` lacks a read handler and cannot confirm existing state. The `!Ref ServerInstance` shared dependency caused CFN to re-process the attachment alongside the EIP change. Stack rolled back cleanly. Concluded the EIP cannot be saved via this route and recommended accepting the one IP change: teardown, import volume only into new persistent stack (fresh EIP), rebuild.

**User:** I agree to teardown and release the IP. I suspect we're looking at a weakness in the old game stack template where it does not tolerate even a minor change over a built stack. Analyze the nature of the breakage to determine any lessons we can carry forward against the persistent + game stack architecture. No need to attempt further recovery before teardown.

**Assistant:** Produced a root cause analysis: (1) `AWS::EC2::VolumeAttachment` predates CFN's modern handler framework and has no read/describe operation, so CFN cannot diff current state and defaults to re-issuing `attach_volume()` on any update; (2) shared `!Ref ServerInstance` dependency causes `PersistentVolumeAttachment` to be re-evaluated whenever `ServerEIP` is marked modified. Explained why the new architecture sidesteps this: the game stack is always recreated (never updated), and the persistent stack contains only `AWS::EC2::Volume` and `AWS::EC2::EIP` which have solid update semantics. Noted the residual risk: if the game stack ever needed an in-place update, `EIPAssociation` carries the same risk as `VolumeAttachment`. Recommended carrying forward the recreate-only design principle.

**User:** I see VolumeAttachment in use before and after the persistent stack was introduced. I see EIPAssociation in use after. I see you calling out the future continuance of the exposure and describing the recreate-only strategy as our appropriate response. Annotate the game stack yaml with Metadata outlining the nature of the update-only constraint. Further add TODO metadata listing future analysis to check for ways to factor out the update-only constraint. Proceed with the architecture as written. Next step: teardown the stack.

**Assistant:** Added a `Metadata:` section to `cloudformation_server_stack.yaml` documenting the update-immutability constraint (root cause, recreate-only mandate) and a TODO for future investigation. Then issued the teardown command.

**User:** [Request interrupted by user for tool use]

I think there's an exposure in the recreate script as written — it does not break out a control for delete-only. I could execute the deletion-only, but let's address the script feature now. I note that the script behavior is already okay when the stack does not exist for deletion. Add the create suppression arg (default delete-create).

**Assistant:** Read `reinstall_stack.py`. Identified three changes needed: `--delete-only` flag, post-parse `--setup-command` required validation, early exit after deletion. Applied all three edits and read the full script to verify the flow. Provided the ready-to-run command.

**User:** [pasted: command output showing `deleted: null` with an UPDATE_ROLLBACK_COMPLETE stack still live in the console]  That null looks like a potential problem. Status of GameStack on AWS Console: UPDATE_ROLLBACK_COMPLETE. Investigate please.

**Assistant:** Searched the stack filter in `find_game_stacks`. Found `UPDATE_ROLLBACK_COMPLETE` missing from the list — the script treated the live stack as non-existent. Fixed the filter to include `UPDATE_ROLLBACK_COMPLETE` (a normal resting state after a failed update). Instructed re-run of the same command.

**User:** deletion succeeded. ready to try creation of the persistent stack. I'll try dry run first. Give a command invocation.

**Assistant:** Provided: `python bin/setup_persistent_stack.py --import-volume-id vol-0f4cee5cb4bc42932` (no `--execute` = dry run; no `--import-allocation-id` = fresh EIP).

**User:** [pasted: dry run output plus two failed creation attempts — first failed with "Template file not found at ../persistent-resources.yaml", second failed with ValidationError: Resources [PersistentEIP] is missing from ResourceToImport list]  Looks like the second creation attempt might be surfacing a template issue?

**Assistant:** Identified two bugs: (1) template path `"../persistent-resources.yaml"` is CWD-relative — breaks when run from the project root; fix with `__file__`-relative resolution (same latent bug in `reinstall_stack.py`). (2) CFN's IMPORT changeset on a new stack requires every template resource in `ResourcesToImport` — `PersistentEIP` was absent because it was being created fresh. Fix: two-step approach — `create_stack` with a partial template (EIP only), then IMPORT the volume into the existing stack. Began editing `setup_persistent_stack.py`.

*(Session ended mid-edit.)*

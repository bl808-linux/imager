# imager

`imager` records which source revisions, patches, and settings are used to build
images for the BL808 M1s Dock. The goal is to return to an experiment branch,
recover its build inputs, and rebuild an image under the same conditions.
Generated binaries are stored separately from Git source history.

In `../m1sdock`, component patches, board test settings, and uncommitted changes
became mixed together. This made it difficult to identify which combination
produced a working image. `imager` is intended to make that combination explicit
and keep each experiment reproducible.

```text
imager/main
  Shared tooling and upstream baselines
        |
        +-- Experiment branch: opensbi-uart
              +-- OpenSBI revision with the UART patch
              +-- Board test configuration / fragments
              +-- Instructions for building the image
```

This diagram describes the intended organization. The `opensbi-uart` experiment
is an example, not an experiment already provided by this repository.

## What each repository branch records

The top-level repository currently includes four Git submodules: `firmware`,
`linux`, `u-boot`, and `opensbi`. Each submodule entry records an exact component
commit.

| Branch | Responsibility |
| --- | --- |
| `imager/main` | Shared tooling and baseline component revisions. Build orchestration is planned. |
| An `imager` experiment branch, such as `opensbi-uart` | The combination of component commits, board settings, configuration fragments, and build instructions for one experiment. |
| A component branch, such as an OpenSBI UART branch | Changes to that component's source, including the UART patch and its commit history. |

These are separate Git repositories. An `imager` branch selects a component
commit through its submodule entry; it does not contain the component branch's
source history. Component branch names help organize development, while the
recorded commits identify the exact source used for a build.

Switching the top-level branch alone does not update the component checkouts.
Restoring an experiment also requires updating the submodules to their recorded
commits. Changes needed to reproduce a build must be committed in the appropriate
repository rather than left only in a working directory.

## Example: an OpenSBI UART experiment

The intended workflow below uses ordinary Git and the existing component build
and flashing tools. Experiment automation, configuration layout, image assembly,
and artifact archival in `imager` are **planned**. There are no implemented
`imager` commands for these steps yet; the current CLI only prints a greeting.

1. **Create an experiment branch.** Start an `imager` branch such as
   `opensbi-uart` from `main`. This branch will record the complete combination
   of inputs for the UART experiment.
2. **Prepare the patch and settings.** Develop and commit the UART patch on an
   OpenSBI component branch. Record that commit in the experiment's OpenSBI
   submodule entry. Save the board test settings, configuration fragments, and
   build instructions in the experiment branch, together with the revisions of
   any other required components. The directory layout for these files is
   **planned**.
3. **Build the image.** Build the selected components with their existing build
   systems, using the recorded settings. Document the toolchain versions,
   commands, and image assembly steps needed to repeat the build. Automated
   preparation, building, and image assembly through `imager` are **planned**.
4. **Archive the image separately.** Keep generated binaries outside Git source
   history. Associate each image with the `imager` commit, component commits,
   build settings, toolchain versions, and a checksum so it can be traced back
   to its inputs. An archive location and automated metadata capture are
   **planned**; no artifact storage service is configured here.
5. **Flash with the existing tools.** Use the archived image with the established
   tooling in `../m1sdock`, such as `bdb` and its `blflashcommand` backend.
   Follow the [existing flashing instructions](../m1sdock/README.md), selecting
   the image layout and flash addresses appropriate to the experiment. Record
   the board test result alongside the experiment's build notes.

Returning to the experiment means restoring its recorded component commits,
settings, and build environment, then repeating its documented build steps.
The branch preserves the recipe; the separate archive preserves the resulting
binaries.

## Worktrees are build workspaces

A Git worktree checks out the same component repository into another directory.
It shares that repository's Git history while keeping a separate working
directory and index. For example, an OpenSBI worktree can provide a dedicated
checkout for building the UART experiment.

Here, worktrees are intended only as build workspaces. The experiment remains
defined by committed component revisions and the settings in its `imager`
branch. A worktree's local changes or build outputs are not a substitute for
those records. Automatic creation and management of build worktrees are
**planned**.

# Maintained V8 patches

The V8 Build workflow applies each `*.patch` file in this directory to the
exact V8 revision before building. A patch that neither applies nor is already
present (applies in reverse) fails the build closed, which blocks every
automated Stable V8 upgrade until the patch is updated or retired.

Retire a patch as soon as the targeted Stable V8 revision contains the fix or
no longer needs it, and record the V8 version and evidence in the commit.

Keep this file even when no patch is maintained: the build key hashes this
directory, so it must exist.

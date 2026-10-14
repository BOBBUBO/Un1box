/* XPwn's exit paths become failures at this C-only boundary. A fatal parser
 * failure poisons this process: the adapter cannot unwind XPwn's raw allocations.
 * Dispose of the dedicated firmware service process after terminal failure. */
#include <setjmp.h>
#include <stdlib.h>
#include <stdio.h>
#include <dmg/dmg.h>
#include <dmg/filevault.h>
extern char endianness;
void lt_fw_init_byte_order(void) {
    short word = 1;
    endianness = *(char *)&word ? IS_LITTLE_ENDIAN : IS_BIG_ENDIAN;
}
static _Thread_local jmp_buf failure;
static _Thread_local int guarded;
static int poisoned;
int lt_fw_xpwn_poisoned(void) { return poisoned; }
extern int lt_fw_hfs_command(int, char **);
_Noreturn void lt_fw_xpwn_fatal(int status) {
    (void)status;
    poisoned = 1;
    if (guarded)
        longjmp(failure, 1);
    abort();
}
int lt_fw_guard_hfs(int argc, char **argv) {
    if (poisoned)
        return 1;
    guarded = 1;
    if (setjmp(failure)) {
        guarded = 0;
        return 1;
    }
    int result = lt_fw_hfs_command(argc, argv);
    guarded = 0;
    return result;
}
int lt_fw_guard_dmg(const char *source, const char *dest, const char *key) {
    if (poisoned)
        return 0;
    guarded = 1;
    if (setjmp(failure)) {
        guarded = 0;
        return 0;
    }
    FILE *in = fopen(source, "rb");
    if (!in) {
        guarded = 0;
        return 0;
    }
    FILE *out = fopen(dest, "wb");
    if (!out) {
        fclose(in);
        guarded = 0;
        return 0;
    }
    AbstractFile *a = createAbstractFileFromFile(in), *b = createAbstractFileFromFile(out);
    a = createAbstractFileFromFileVault(a, key);
    int ok = a && extractDmg(a, b, -1);
    guarded = 0;
    return ok;
}

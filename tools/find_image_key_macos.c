/* Scan a live WeChat process for the AES-128 key used by V2 .dat images.
 *
 * Community keys are 16-byte alphanumeric ASCII, present after viewing images.
 * Each candidate is checked by AES-ECB-decrypting the first ciphertext block
 * of a sample V2 .dat and looking for JPEG/PNG/GIF/WEBP/wxgf magic.
 *
 * Usage: sudo ./find_image_key_macos <pid> <sample.dat>
 */
#include <CommonCrypto/CommonCryptor.h>
#include <ctype.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHUNK (8 * 1024 * 1024)
#define V2_MAGIC "\x07\x08V2\x08\x07"

static int is_image_magic(const unsigned char *p) {
    if (p[0] == 0xff && p[1] == 0xd8 && p[2] == 0xff) return 1;
    if (p[0] == 0x89 && p[1] == 'P' && p[2] == 'N' && p[3] == 'G') return 1;
    if (p[0] == 'G' && p[1] == 'I' && p[2] == 'F') return 1;
    if (p[0] == 'B' && p[1] == 'M') return 1;
    if (p[0] == 'R' && p[1] == 'I' && p[2] == 'F' && p[3] == 'F') return 1;
    if (p[0] == 'w' && p[1] == 'x' && p[2] == 'g' && p[3] == 'f') return 1;
    return 0;
}

static int is_alnum16(const unsigned char *p) {
    for (int i = 0; i < 16; i++) {
        unsigned char c = p[i];
        if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')))
            return 0;
    }
    return 1;
}

static int try_key(const unsigned char *key, const unsigned char *ct) {
    unsigned char pt[16];
    size_t moved = 0;
    CCCryptorStatus st = CCCrypt(kCCDecrypt, kCCAlgorithmAES128, kCCOptionECBMode,
                                 key, 16, NULL, ct, 16, pt, 16, &moved);
    return st == kCCSuccess && moved == 16 && is_image_magic(pt);
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <pid> <sample.dat>\n", argv[0]);
        return 2;
    }
    pid_t pid = (pid_t)atoi(argv[1]);
    FILE *fp = fopen(argv[2], "rb");
    if (!fp) { perror(argv[2]); return 2; }
    unsigned char hdr[31];
    if (fread(hdr, 1, 31, fp) != 31) { fprintf(stderr, "dat too short\n"); return 2; }
    fclose(fp);
    if (memcmp(hdr, V2_MAGIC, 6) != 0) { fprintf(stderr, "not V2 dat\n"); return 2; }
    const unsigned char *ct = hdr + 15;

    task_t task;
    kern_return_t kr = task_for_pid(mach_task_self(), pid, &task);
    if (kr != KERN_SUCCESS) {
        fprintf(stderr, "[!] task_for_pid failed: %s (%d)\n", mach_error_string(kr), kr);
        return 3;
    }
    fprintf(stderr, "[*] pid=%d\n", pid);

    unsigned char *chunk = malloc(CHUNK);
    if (!chunk) return 4;

    unsigned char seen[64][16];
    int nseen = 0;
    unsigned long long scanned = 0, tests = 0;
    mach_vm_address_t address = 0, size = 0;
    natural_t depth = 0;

    while (1) {
        vm_region_submap_info_data_64_t info;
        mach_msg_type_number_t cnt = VM_REGION_SUBMAP_INFO_COUNT_64;
        kr = mach_vm_region_recurse(task, &address, &size, &depth,
                                    (vm_region_recurse_info_t)&info, &cnt);
        if (kr != KERN_SUCCESS) break;
        if (info.is_submap) { depth++; continue; }
        if (!(info.protection & VM_PROT_READ)) { address += size; continue; }

        mach_vm_address_t rend = address + size, a = address;
        while (a < rend) {
            mach_vm_size_t want = rend - a;
            if (want > CHUNK) want = CHUNK;
            mach_vm_size_t got = 0;
            kr = mach_vm_read_overwrite(task, a, want, (mach_vm_address_t)chunk, &got);
            if (kr != KERN_SUCCESS || got < 16) { a += 4096; continue; }
            scanned += got;
            for (size_t i = 0; i + 16 <= (size_t)got; i++) {
                if (!is_alnum16(chunk + i)) continue;
                tests++;
                if (!try_key(chunk + i, ct)) continue;
                int dup = 0;
                for (int s = 0; s < nseen; s++)
                    if (memcmp(seen[s], chunk + i, 16) == 0) { dup = 1; break; }
                if (dup) continue;
                if (nseen < 64) memcpy(seen[nseen++], chunk + i, 16);
                printf("KEY ");
                for (int k = 0; k < 16; k++) printf("%02x", chunk[i + k]);
                printf(" ascii=");
                fwrite(chunk + i, 1, 16, stdout);
                printf("\n");
                fflush(stdout);
            }
            a += got;
        }
        address += size;
    }
    fprintf(stderr, "[*] scanned %.1f MiB, tests=%llu, hits=%d\n",
            scanned / 1048576.0, tests, nseen);
    return nseen ? 0 : 1;
}

// find_keys_macos.c — extract WeChat 4.x (WCDB/SQLCipher4) key material from a
// running WeChat process on macOS. Two complementary methods in one pass:
//
//   (A) literal scan : find  x'<64hex enc_key><32hex salt>'  (upper or lower hex)
//   (B) salt-anchored: given each DB's known 16-byte salt (first 16 bytes of the
//                       .db file), find that salt in memory and dump a window of
//                       surrounding bytes. The enc key lives in the same WCDB
//                       codec struct, so the raw 32-byte key is nearby. Python
//                       then slides candidates over the window and HMAC-verifies.
//
// Build:  cc -O2 -o find_keys_macos find_keys_macos.c -framework Foundation
// Run:    sudo ./find_keys_macos <salts.txt> [pid]
//         pid auto-detects ~/.ppchat/extract/WeChat.app only (never /Applications)
//
// Output (stdout, JSON): {"literals":[...], "windows":[{"salt":"..","ctx":".."}]}
// Progress on stderr.

#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <libproc.h>
#include <sys/sysctl.h>

#define CHUNK   (8 * 1024 * 1024)
#define WINDOW  2048            // bytes captured on each side of a salt hit
#define OVERLAP (2 * WINDOW)    // guarantees full window in some chunk
#define PAT_LEN 99             // x' + 96 hex + '
#define SALT_LEN 16

static int is_hex(unsigned char c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

// ---- dedup set of literal strings ----
static char **lits = NULL; static size_t lits_n = 0, lits_cap = 0;
static int lit_seen(const char *s){for(size_t i=0;i<lits_n;i++)if(!strcmp(lits[i],s))return 1;return 0;}
static void lit_add(const char *s){
    if(lit_seen(s))return;
    if(lits_n==lits_cap){lits_cap=lits_cap?lits_cap*2:32;lits=realloc(lits,lits_cap*sizeof(char*));}
    lits[lits_n++]=strdup(s);
}

// ---- salts ----
static unsigned char (*salts)[SALT_LEN] = NULL;
static char (*salts_hex)[33] = NULL;
static size_t salts_n = 0;

static void load_salts(const char *path){
    FILE *f = fopen(path,"r");
    if(!f){fprintf(stderr,"[!] cannot open salts file %s\n",path);return;}
    char line[128];
    while(fgets(line,sizeof(line),f)){
        // strip whitespace
        char hex[64]; int h=0;
        for(char *p=line; *p && h<32; p++) if(is_hex((unsigned char)*p)) hex[h++]=*p;
        if(h!=32) continue;
        salts = realloc(salts, (salts_n+1)*SALT_LEN);
        salts_hex = realloc(salts_hex, (salts_n+1)*33);
        for(int i=0;i<16;i++){ unsigned v; sscanf(hex+2*i,"%2x",&v); salts[salts_n][i]=(unsigned char)v; }
        memcpy(salts_hex[salts_n],hex,32); salts_hex[salts_n][32]=0;
        salts_n++;
    }
    fclose(f);
    fprintf(stderr,"[*] loaded %zu salts\n",salts_n);
}

// print a byte range as hex to stdout
static void print_hex(const unsigned char *p, size_t n){
    static const char *H="0123456789abcdef";
    for(size_t i=0;i<n;i++){ putchar(H[p[i]>>4]); putchar(H[p[i]&0xf]); }
}

static int first_window = 1;

static void scan_chunk(const unsigned char *buf, size_t n){
    // (A) literals
    if(n >= PAT_LEN){
        for(size_t i=0;i+PAT_LEN<=n;i++){
            if(buf[i]=='x' && buf[i+1]=='\'' && buf[i+PAT_LEN-1]=='\''){
                int ok=1; for(int j=0;j<96;j++){ if(!is_hex(buf[i+2+j])){ok=0;break;} }
                if(ok){ char hb[97]; for(int j=0;j<96;j++) hb[j]=tolower(buf[i+2+j]); hb[96]=0; lit_add(hb);}
            }
        }
    }
    // (B) salt-anchored windows
    for(size_t s=0;s<salts_n;s++){
        const unsigned char *hay = buf; size_t remain = n;
        while(remain >= SALT_LEN){
            const unsigned char *hit = memmem(hay, remain, salts[s], SALT_LEN);
            if(!hit) break;
            size_t pos = (size_t)(hit - buf);
            size_t start = pos > WINDOW ? pos - WINDOW : 0;
            size_t end = pos + SALT_LEN + WINDOW; if(end > n) end = n;
            if(!first_window) printf(",");
            first_window = 0;
            printf("{\"salt\":\"%s\",\"ctx\":\"", salts_hex[s]);
            print_hex(buf+start, end-start);
            printf("\"}");
            hay = hit + SALT_LEN; remain = n - (size_t)(hay - buf);
        }
    }
}

static pid_t find_wechat_pid(void){
    /* Only the extract copy: ~/.ppchat/extract/WeChat.app
       Never attach to /Applications/WeChat.app. */
    int mib[4]={CTL_KERN,KERN_PROC,KERN_PROC_ALL,0}; size_t len=0;
    if(sysctl(mib,4,NULL,&len,NULL,0)<0)return 0;
    struct kinfo_proc *procs=malloc(len);
    if(!procs)return 0;
    if(sysctl(mib,4,procs,&len,NULL,0)<0){free(procs);return 0;}
    size_t count=len/sizeof(struct kinfo_proc); pid_t r=0;
    for(size_t i=0;i<count;i++){
        pid_t pid=procs[i].kp_proc.p_pid; char path[PROC_PIDPATHINFO_MAXSIZE];
        if(proc_pidpath(pid,path,sizeof(path))>0){
            if(strstr(path,"/.ppchat/extract/WeChat.app/Contents/MacOS/WeChat") &&
               !strstr(path,"Helper") && !strstr(path,"WeChatAppEx")){ r=pid; break; }
        }
    }
    free(procs); return r;
}

int main(int argc,char**argv){
    if(argc<2){ fprintf(stderr,"usage: %s <salts.txt> [pid]\n",argv[0]); return 2; }
    load_salts(argv[1]);
    pid_t pid = argc>=3 ? (pid_t)atoi(argv[2]) : find_wechat_pid();
    if(pid==0){
        fprintf(stderr,"[!] extract-copy WeChat not running (~/.ppchat/extract/WeChat.app)\n");
        fprintf(stderr,"    run: bash tools/get_keys.sh prepare\n");
        fprintf(stderr,"    then launch the copy; never codesign /Applications/WeChat.app\n");
        return 2;
    }
    fprintf(stderr,"[*] target WeChat pid = %d\n",pid);

    task_t task;
    kern_return_t kr = task_for_pid(mach_task_self(), pid, &task);
    if(kr!=KERN_SUCCESS){
        fprintf(stderr,"[!] task_for_pid failed: %s (%d). Run with sudo against the extract copy.\n",
                mach_error_string(kr),kr);
        return 3;
    }

    unsigned char *chunk = malloc(CHUNK);
    if(!chunk){fprintf(stderr,"oom\n");return 4;}

    printf("{\"windows\":[");

    mach_vm_address_t address=0; mach_vm_size_t size=0; natural_t depth=0;
    unsigned long long scanned=0; unsigned regions=0;

    while(1){
        vm_region_submap_info_data_64_t info;
        mach_msg_type_number_t cnt=VM_REGION_SUBMAP_INFO_COUNT_64;
        kr=mach_vm_region_recurse(task,&address,&size,&depth,(vm_region_recurse_info_t)&info,&cnt);
        if(kr!=KERN_SUCCESS) break;
        if(info.is_submap){ depth++; continue; }

        if(info.protection & VM_PROT_READ){
            regions++;
            mach_vm_address_t rend = address + size;
            mach_vm_address_t a = address;
            while(a < rend){
                mach_vm_size_t want = rend - a; if(want>CHUNK) want=CHUNK;
                mach_vm_size_t got=0;
                kr = mach_vm_read_overwrite(task,a,want,(mach_vm_address_t)chunk,&got);
                if(kr==KERN_SUCCESS && got>0){
                    scan_chunk(chunk,(size_t)got);
                    scanned+=got;
                    if(got<=OVERLAP){ a += got; }        // small tail: move fully on
                    else { a += (got - OVERLAP); }        // keep overlap for boundary
                } else {
                    a += 4096;   // unreadable page: skip it, DO NOT abandon region
                }
            }
        }
        address += size;
    }

    printf("],\"literals\":[");
    for(size_t i=0;i<lits_n;i++) printf("%s\"%s\"", i?",":"", lits[i]);
    printf("]}\n");

    fprintf(stderr,"[*] scanned %.1f MiB across %u readable regions; %zu literal(s), windows emitted inline\n",
            scanned/1048576.0, regions, lits_n);
    return 0;
}

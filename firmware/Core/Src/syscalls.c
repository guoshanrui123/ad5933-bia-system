#include <sys/stat.h>
#include <errno.h>

int _close(int file) { return -1; }
int _fstat(int file, struct stat *st) { st->st_mode = S_IFCHR; return 0; }
int _isatty(int file) { return 1; }
int _lseek(int file, int ptr, int dir) { return 0; }
int _read(int file, char *ptr, int len) { return 0; }
int _write(int file, char *ptr, int len) { return len; }
int _kill(int pid, int sig) { errno = EINVAL; return -1; }
int _getpid(void) { return 1; }

void *_sbrk(int incr)
{
    extern char end;
    static char *heap_end;
    char *prev_heap_end;

    if (heap_end == 0) {
        heap_end = &end;
    }

    prev_heap_end = heap_end;
    heap_end += incr;
    return (void *)prev_heap_end;
}

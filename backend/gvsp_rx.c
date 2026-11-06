/* GVSP frame assembler used by the camera bridge through ctypes.
 *
 * The receiver owns no socket: it reads from a UDP descriptor created before
 * the bridge entered its seccomp sandbox. Packets from any source other than
 * the camera are discarded. Missing packets leave the previous frame's bytes
 * in place, so a rare loss shows up as a small stale band instead of a dropped
 * frame; the caller gets the exact missing count and decides what to do.
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>

#define BATCH 64
#define PKT_MAX 2048

typedef struct {
    uint32_t block, payload_type, pixel_format, width, height, bytes;
    uint32_t expected, received, missing;
    uint64_t timestamp;
    uint64_t total_packets, total_foreign;
} GvspInfo;

typedef struct {
    int fd;
    uint32_t camera_ip;
    uint32_t packet_payload, capacity, payload;
    uint8_t *frame;
    uint8_t *seen;
    uint32_t seen_bytes;
    int active;
    GvspInfo cur;
    uint64_t total_packets, total_foreign;
    struct mmsghdr msgs[BATCH];
    struct iovec iov[BATCH];
    struct sockaddr_in from[BATCH];
    uint8_t *pkts;
    int batch_count, batch_at;
} Gvsp;

static uint16_t get16(const uint8_t *p) { return (uint16_t)((p[0] << 8) | p[1]); }
static uint32_t get32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

Gvsp *gvsp_open(int fd, uint32_t camera_ip_be) {
    Gvsp *g = calloc(1, sizeof(*g));
    if (!g) return NULL;
    g->pkts = malloc(BATCH * PKT_MAX);
    if (!g->pkts) { free(g); return NULL; }
    g->fd = fd;
    g->camera_ip = camera_ip_be;
    for (int i = 0; i < BATCH; ++i) {
        g->iov[i].iov_base = g->pkts + i * PKT_MAX;
        g->iov[i].iov_len = PKT_MAX;
        g->msgs[i].msg_hdr.msg_iov = &g->iov[i];
        g->msgs[i].msg_hdr.msg_iovlen = 1;
        g->msgs[i].msg_hdr.msg_name = &g->from[i];
        g->msgs[i].msg_hdr.msg_namelen = sizeof(g->from[i]);
    }
    return g;
}

/* Reallocates only when growing, keeping the previous pixels as fill. */
int gvsp_configure(Gvsp *g, uint32_t capacity, uint32_t packet_payload) {
    if (!packet_payload) return -1;
    if (capacity > g->capacity) {
        uint8_t *frame = realloc(g->frame, capacity);
        if (!frame) return -1;
        memset(frame + g->capacity, 0, capacity - g->capacity);
        g->frame = frame;
        g->capacity = capacity;
    }
    uint32_t packets = capacity / packet_payload + 2;
    uint32_t seen_bytes = packets / 8 + 1;
    if (seen_bytes > g->seen_bytes) {
        uint8_t *seen = realloc(g->seen, seen_bytes);
        if (!seen) return -1;
        g->seen = seen;
        g->seen_bytes = seen_bytes;
    }
    g->packet_payload = packet_payload;
    g->payload = capacity;
    g->active = 0;
    g->batch_count = g->batch_at = 0;
    return 0;
}

static int finish(Gvsp *g, uint8_t *out, uint32_t out_cap, GvspInfo *info) {
    GvspInfo *c = &g->cur;
    g->active = 0;
    if (c->bytes > out_cap || c->bytes > g->capacity) return 0;
    c->expected = (c->bytes + g->packet_payload - 1) / g->packet_payload;
    c->missing = c->received >= c->expected ? 0 : c->expected - c->received;
    memcpy(out, g->frame, c->bytes);
    c->total_packets = g->total_packets;
    c->total_foreign = g->total_foreign;
    *info = *c;
    return 1;
}

/* Returns 1 with a frame, 0 on timeout, negative on socket error.
 * A leader that interrupts an unfinished block finishes that block first and
 * is re-processed on the next call. */
int gvsp_next(Gvsp *g, uint8_t *out, uint32_t out_cap, int timeout_ms, GvspInfo *info) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    int64_t deadline = now.tv_sec * 1000LL + now.tv_nsec / 1000000 + timeout_ms;
    for (;;) {
        if (g->batch_at >= g->batch_count) {
            clock_gettime(CLOCK_MONOTONIC, &now);
            int64_t left = deadline - (now.tv_sec * 1000LL + now.tv_nsec / 1000000);
            if (left < 0) left = 0;
            struct pollfd p = {g->fd, POLLIN, 0};
            int r = poll(&p, 1, (int)left);
            if (r < 0) { if (errno == EINTR) continue; return -errno; }
            if (r == 0) return 0;
            for (int i = 0; i < BATCH; ++i) g->msgs[i].msg_hdr.msg_namelen = sizeof(g->from[i]);
            r = recvmmsg(g->fd, g->msgs, BATCH, MSG_DONTWAIT, NULL);
            if (r < 0) {
                if (errno == EAGAIN || errno == EINTR) continue;
                return -errno;
            }
            g->batch_count = r;
            g->batch_at = 0;
        }
        int i = g->batch_at;
        const uint8_t *d = g->pkts + i * PKT_MAX;
        uint32_t n = g->msgs[i].msg_len;
        if (g->from[i].sin_addr.s_addr != g->camera_ip || n < 8) {
            ++g->total_foreign;
            ++g->batch_at;
            continue;
        }
        uint32_t block = get16(d + 2), word = get32(d + 4);
        uint32_t kind = word >> 24, id = word & 0xffffff;
        if (block == 0) { ++g->batch_at; continue; }

        if (kind == 1) {
            if (g->active) return finish(g, out, out_cap, info);
            ++g->batch_at;
            ++g->total_packets;
            if (n < 40) continue;
            memset(&g->cur, 0, sizeof(g->cur));
            g->cur.block = block;
            g->cur.payload_type = get16(d + 10);
            g->cur.timestamp = ((uint64_t)get32(d + 12) << 32) | get32(d + 16);
            if (g->cur.payload_type == 6) {
                g->cur.bytes = get32(d + 24); /* JPEG leader: actual encoded size */
            } else {
                if (n < 44) continue;
                g->cur.pixel_format = get32(d + 20);
                g->cur.width = get32(d + 24);
                g->cur.height = get32(d + 28);
                g->cur.bytes = g->payload;
            }
            memset(g->seen, 0, g->seen_bytes);
            g->active = 1;
            continue;
        }
        ++g->batch_at;
        ++g->total_packets;
        if (!g->active || block != g->cur.block) continue;
        if (kind == 3) {
            if (id == 0) continue;
            uint64_t at = (uint64_t)(id - 1) * g->packet_payload;
            uint32_t len = n - 8;
            if (at + len > g->capacity) continue;
            uint32_t bit = id - 1;
            if (bit / 8 >= g->seen_bytes) continue;
            if (!(g->seen[bit / 8] & (1u << (bit & 7)))) {
                g->seen[bit / 8] |= (uint8_t)(1u << (bit & 7));
                ++g->cur.received;
            }
            memcpy(g->frame + at, d + 8, len);
        } else if (kind == 2) {
            return finish(g, out, out_cap, info);
        }
    }
}

void gvsp_close(Gvsp *g) {
    if (!g) return;
    free(g->frame);
    free(g->seen);
    free(g->pkts);
    free(g);
}

/* SPDX-License-Identifier: GPL-2.0 */
/* RAM-only autonomous printk and platform-unbind smoke test. */
#define _GNU_SOURCE
#include <dirent.h>
#include <poll.h>
#include <termios.h>
#include <sys/mman.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef SMOKE_TAG
#error SMOKE_TAG must identify the accepted artifact set
#endif
#ifndef SMOKE_TTY
#define SMOKE_TTY "ttyBFLB0"
#endif
#ifndef SMOKE_DEVICE
#define SMOKE_DEVICE "30002000"
#endif
static int kmsg;
static const char driver[] = "/sys/bus/platform/drivers/bflb_uart";

static void emit(const char *format, ...)
{
	char payload[1024], record[1200];
	va_list args;
	va_start(args, format);
	vsnprintf(payload, sizeof(payload), format, args);
	va_end(args);
	int length = snprintf(record, sizeof(record), "<6>BL808_SMOKE_%s %s\n", SMOKE_TAG, payload);
	if (write(kmsg, record, length) != length)
		_exit(91);
}

static void fail(const char *operation)
{
	emit("FAIL %s errno=%d", operation, errno);
	for (;;)
		pause();
}

static void report(const char *path, const char *name)
{
	char buffer[4096];
	int fd = open(path, O_RDONLY);
	if (fd < 0)
		fail(path);
	ssize_t length = read(fd, buffer, sizeof(buffer) - 1);
	if (length < 0)
		fail(path);
	buffer[length] = 0;
	close(fd);
	char *save, *line = strtok_r(buffer, "\n", &save);
	while (line) {
		emit("%s %s", name, line);
		line = strtok_r(NULL, "\n", &save);
	}
}

static void control(const char *action, const char *device)
{
	char path[256];
	snprintf(path, sizeof(path), "%s/%s", driver, action);
	int fd = open(path, O_WRONLY);
	if (fd < 0)
		fail(path);
	if (write(fd, device, strlen(device)) != (ssize_t)strlen(device))
		fail(action);
	close(fd);
}

int main(void)
{
	if (mount("proc", "/proc", "proc", 0, NULL) && errno != EBUSY)
		return 92;
	if (mount("sysfs", "/sys", "sysfs", 0, NULL) && errno != EBUSY)
		return 93;
	if (mount("devtmpfs", "/dev", "devtmpfs", 0, NULL) && errno != EBUSY)
		return 94;
	kmsg = open("/dev/kmsg", O_WRONLY);
	if (kmsg < 0)
		return 95;
	emit("START");
	report("/proc/cmdline", "CMDLINE");
	report("/proc/consoles", "CONSOLE_BEFORE");
	report("/proc/tty/driver/bflb_uart", "UART_INFO");
	report("/proc/interrupts", "IRQ_BEFORE");

    int tty = open("/dev/" SMOKE_TTY, O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (tty < 0) fail("TTY open");
    struct termios settings;
    if (tcgetattr(tty, &settings)) fail("tcgetattr");
    cfmakeraw(&settings);
    settings.c_cflag |= CLOCAL | CREAD;
    cfsetispeed(&settings, B115200); cfsetospeed(&settings, B115200);
    if (tcsetattr(tty, TCSANOW, &settings) || tcflush(tty, TCIOFLUSH)) fail("raw termios");
    char payload[4096], received[4096];
    for (unsigned int i = 0; i < sizeof(payload); i++) payload[i] = 'A' + i % 26;
    emit("TTY_TX_BEGIN");
    for (unsigned int offset = 0; offset < sizeof(payload);) {
        ssize_t n = write(tty, payload + offset, sizeof(payload) - offset);
        if (n > 0) offset += n;
        else if (n < 0 && errno == EAGAIN) {
            struct pollfd f = { .fd = tty, .events = POLLOUT };
            if (poll(&f, 1, 30000) <= 0) fail("TX timeout");
        } else fail("TTY write");
    }
    if (tcdrain(tty)) fail("tcdrain");
    emit("TTY_TX_END");
    emit("TTY_RX_READY");
    for (unsigned int offset = 0; offset < sizeof(received);) {
        struct pollfd f = { .fd = tty, .events = POLLIN };
        if (poll(&f, 1, 30000) <= 0) fail("RX timeout");
        ssize_t n = read(tty, received + offset, sizeof(received) - offset);
        if (n > 0) offset += n;
        else if (n < 0 && errno == EAGAIN) continue;
        else fail("TTY read");
    }
    if (memcmp(payload, received, sizeof(payload))) fail("RX data mismatch");
    emit("TTY_RX_OK 4096");
    close(tty);

	char device[128] = {0};
	DIR *directory = opendir(driver);
	if (!directory)
		fail("driver directory");
	struct dirent *entry;
	while ((entry = readdir(directory))) {
		if (strstr(entry->d_name, SMOKE_DEVICE) && strlen(entry->d_name) < sizeof(device))
			strcpy(device, entry->d_name);
	}
	closedir(directory);
	if (!device[0])
		fail("UART device missing");

	int bound_fd = open("/sys/bus/platform/devices/30007000.reset-controller/test", O_WRONLY);
	if (bound_fd < 0)
		fail("reset test endpoint");
	errno = 0;
	if (write(bound_fd, "1", 1) != -1 || errno != EBUSY)
		fail("reset accepted while UART bound");
	close(bound_fd);
	emit("RESET_BOUND_REJECTED errno=%d", EBUSY);

	struct timespec begin, end;
	clock_gettime(CLOCK_MONOTONIC, &begin);
	int ring = open("/dev/kmsg", O_RDONLY | O_NONBLOCK);
	if (ring < 0 || lseek(ring, 0, SEEK_END) < 0)
		fail("kmsg reader");
	pid_t child = fork();
	if (child < 0)
		fail("fork");
	if (!child) {
		for (int i = 0; i < 32; i++) {
			emit("RACE %02d abcdefghijklmnopqrstuvwxyz", i);
			usleep(5000);
		}
		_exit(0);
	}
	int reset_failures = 0;
	for (int i = 0; i < 8; i++) {
        int held = open("/dev/" SMOKE_TTY, O_RDWR | O_NOCTTY | O_NONBLOCK);
        if (held < 0) fail("held FD open");
        control("unbind", device);
        char ch;
        errno = 0;
        ssize_t gone = read(held, &ch, 1);
        if (gone != 0 && !(gone < 0 && errno == EIO)) fail("hung-up FD read");
        close(held);
		int reset_fd = open("/sys/bus/platform/devices/30007000.reset-controller/test", O_WRONLY);
		int reset_errno = 0;
		if (reset_fd < 0) {
			reset_errno = errno;
		} else {
			if (write(reset_fd, "1", 1) != 1)
				reset_errno = errno;
			close(reset_fd);
		}
        int mem = open("/dev/mem", O_RDWR | O_SYNC);
        if (mem < 0) fail("open devmem");
        volatile unsigned int *uart = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, mem, 0x30002000);
        if (uart == MAP_FAILED) fail("mmap UART");
        unsigned int saved_tx = uart[0];
        uart[0] = saved_tx & ~1U;
        uart[0x80 / 4] = (1U << 2) | (1U << 3);
        uart[0x80 / 4] = 0;
        uart[0x88 / 4] = '!';
        __asm__ volatile ("fence iorw, iorw" ::: "memory");
        if ((uart[0x84 / 4] & 0x3f) != 31) fail("FIFO canary not queued");
        control("bind", device);
        if ((uart[0x84 / 4] & 0x3fff) != 32 || (uart[0x80 / 4] & 3)) fail("FIFO not initialized");
        munmap((void *)uart, 4096); close(mem);
        emit("FIFO_INIT_OK %d", i);
        emit("HELD_FD_CLOSED %d", i);
        int reopened = open("/dev/" SMOKE_TTY, O_RDWR | O_NOCTTY | O_NONBLOCK);
        if (reopened < 0) fail("reopen after rebind");
        if (write(reopened, "TTY_REBIND_OK\n", 14) != 14 || tcdrain(reopened)) fail("rebind TX");
        close(reopened);
		report("/sys/bus/platform/devices/30007000.reset-controller/test", "RESET_RESULT");
		if (reset_errno) {
			reset_failures++;
			emit("RESET_FAILURE errno=%d", reset_errno);
		}
		emit("CYCLE %d", i);
		usleep(25000);
	}
	int status;
	if (waitpid(child, &status, 0) != child || status)
		fail("writer child");
	unsigned int seen = 0;
	char record[8192], expected[128];
	ssize_t count;
	while ((count = read(ring, record, sizeof(record) - 1)) >= 0) {
		record[count] = 0;
		for (int i = 0; i < 32; i++) {
			snprintf(expected, sizeof(expected), "BL808_SMOKE_%s RACE %02d abcdefghijklmnopqrstuvwxyz\n", SMOKE_TAG, i);
			if (strstr(record, expected))
				seen |= 1U << i;
		}
	}
	if (errno != EAGAIN || seen != 0xffffffffU)
		fail("missing/corrupt racing kmsg record");
	close(ring);
	emit("RACE_RING_OK 32");
	/* Verify complete wire output while the console is registered. During
	 * unbind, printk may intentionally skip wire replay after SBI handoff. */
	for (int i = 0; i < 32; i++)
		emit("RECORD %02d abcdefghijklmnopqrstuvwxyz", i);
	usleep(150000);
	clock_gettime(CLOCK_MONOTONIC, &end);
	long elapsed = (end.tv_sec - begin.tv_sec) * 1000 + (end.tv_nsec - begin.tv_nsec) / 1000000;
	if (elapsed < 200)
		fail("timer progress");
	emit("ELAPSED_MS %ld", elapsed);
	report("/proc/consoles", "CONSOLE_AFTER");
	report("/proc/interrupts", "IRQ_AFTER");
	emit("RESET_FAILURES %d", reset_failures);
	emit("DONE");
	for (;;)
		pause();
}

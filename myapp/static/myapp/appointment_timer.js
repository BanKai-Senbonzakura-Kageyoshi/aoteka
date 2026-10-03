document.querySelectorAll('[data-countdown]').forEach((timer) => {
    const deadline = new Date(timer.dataset.countdown).getTime();
    let intervalId;

    const updateTimer = () => {
        const remainingSeconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
        const minutes = Math.floor(remainingSeconds / 60).toString().padStart(2, '0');
        const seconds = (remainingSeconds % 60).toString().padStart(2, '0');
        timer.textContent = remainingSeconds ? `${minutes}:${seconds}` : 'Время истекло';
        if (!remainingSeconds) window.clearInterval(intervalId);
    };

    updateTimer();
    if (deadline > Date.now()) intervalId = window.setInterval(updateTimer, 1000);
});
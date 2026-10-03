/* Оплата картой в корзине: форматирование ввода и живой визуал карты.
   Данные никуда не отправляются — только обновляют картинку на странице. */
(function () {
    'use strict';

    function onlyDigits(value) {
        return value.replace(/\D/g, '').slice(0, 19);
    }

    function groupNumber(digits) {
        return digits.replace(/(\d{4})(?=\d)/g, '$1 ');
    }

    function detectBrand(digits) {
        if (/^4/.test(digits)) return 'visa';
        if (/^(5[1-5]|2[2-7])/.test(digits)) return 'mastercard';
        return null;
    }

    function pad(value) {
        return value.length === 1 ? '0' + value : value;
    }

    function initPayForm(form) {
        var number = form.querySelector('[data-card-field="number"]');
        var holder = form.querySelector('[data-card-field="holder"]');
        var month = form.querySelector('[data-card-field="month"]');
        var year = form.querySelector('[data-card-field="year"]');
        var cvc = form.querySelector('[data-card-field="cvc"]');
        if (!number) return;

        var echoNumber = form.querySelector('[data-card-echo="number"]');
        var echoHolder = form.querySelector('[data-card-echo="holder"]');
        var echoExpiry = form.querySelector('[data-card-echo="expiry"]');
        var brands = form.querySelectorAll('[data-brand]');

        function renderNumber() {
            var digits = onlyDigits(number.value);
            var grouped = groupNumber(digits);
            if (number.value !== grouped) number.value = grouped;
            if (echoNumber) echoNumber.textContent = grouped || '•••• •••• •••• ••••';

            var brand = detectBrand(digits);
            brands.forEach(function (badge) {
                badge.classList.toggle('is-active', badge.getAttribute('data-brand') === brand);
            });
        }

        function renderHolder() {
            if (!echoHolder) return;
            var text = (holder ? holder.value : '').trim().toUpperCase();
            echoHolder.textContent = text || 'ВАШЕ ИМЯ';
        }

        function renderExpiry() {
            if (!echoExpiry) return;
            var monthValue = month ? month.value : '';
            var yearValue = year ? year.value : '';
            if (!monthValue || !yearValue) {
                echoExpiry.textContent = 'ММ/ГГ';
                return;
            }
            echoExpiry.textContent = pad(monthValue) + '/' + yearValue.slice(-2);
        }

        number.addEventListener('input', renderNumber);
        number.addEventListener('focus', renderNumber);
        if (holder) {
            holder.addEventListener('input', renderHolder);
            holder.addEventListener('focus', renderHolder);
        }
        if (month) {
            month.addEventListener('change', renderExpiry);
            month.addEventListener('focus', renderExpiry);
        }
        if (year) {
            year.addEventListener('change', renderExpiry);
            year.addEventListener('focus', renderExpiry);
        }
        if (cvc) {
            cvc.addEventListener('input', function () {
                cvc.value = cvc.value.replace(/\D/g, '').slice(0, 6);
            });
        }

        renderNumber();
        renderHolder();
        renderExpiry();
    }

    document.querySelectorAll('.pay-form').forEach(initPayForm);
})();

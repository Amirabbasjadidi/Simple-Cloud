document.getElementById('login-form').addEventListener('submit', function(event) {
    event.preventDefault();

    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const rememberMe = document.getElementById('remember-me').checked;
    const captcha = document.getElementById('captcha').value;

    if (!username || !password || !captcha) {
        alert('Please fill in all fields.');
        return;
    }

    fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, remember_me: rememberMe, captcha }),
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            window.location.href = data.redirect_url || '/upload';
        } else {
            alert(data.error || 'Invalid login credentials.');
        }
    })
    .catch(error => {
        console.error('Error during login:', error);
        alert('An error occurred. Please try again.');
    });
});

const hideCharacterCheckbox = document.getElementById('hide-character');
const passwordField = document.getElementById('password');

hideCharacterCheckbox.addEventListener('change', () => {
    passwordField.type = hideCharacterCheckbox.checked ? 'password' : 'text';
});

const captchaImage = document.getElementById('captcha-image');

if (captchaImage) {
    captchaImage.addEventListener('click', function() {
        fetch('/refresh_captcha')
            .then(response => response.json())
            .then(data => {
                captchaImage.src = 'data:image/png;base64,' + data.captcha_image;
            })
            .catch(error => console.error('Error refreshing captcha:', error));
    });
}

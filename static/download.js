const urlInput = document.getElementById("url-list-input");
const fetchBtn = document.getElementById("fetch-btn");
const progressBtn = document.getElementById("progress-btn");
const progressPopup = document.getElementById("download-progress-popup");
const closePopupBtn = document.getElementById("popup-close-btn");
const progressContainer = document.getElementById("download-progress-container");

let urlsToDownload = [];
let isFetching = false;

fetchBtn.addEventListener("click", () => {
    const urlList = urlInput.value.split(",").map((url) => url.trim()).filter((url) => url);
    if (urlList.length > 0 && !isFetching) {
        urlsToDownload = urlList;
        isFetching = true;
        progressBtn.disabled = false;
        startDownload();
    }
});

progressBtn.addEventListener("click", () => {
    progressPopup.style.display = "flex";
});

closePopupBtn.addEventListener("click", () => {
    progressPopup.style.display = "none";
});

async function startDownload() {
    const failedUrls = [];

    for (let i = 0; i < urlsToDownload.length; i += 3) {
        const batch = urlsToDownload.slice(i, i + 3);
        const progressBars = batch.map(createProgressBar);

        await Promise.all(
            batch.map((url, index) => {
                if (!isValidUrl(url)) {
                    failedUrls.push(`${url} - Invalid Format`);
                    return Promise.resolve();
                }

                return downloadFile(url, progressBars[index]).catch((error) => {
                    failedUrls.push(`${url} - ${error}`);
                });
            })
        );
    }

    if (failedUrls.length > 0) {
        downloadFailedUrls(failedUrls);
    }

    isFetching = false;
    checkAllUploadsCompleted();
}

function isValidUrl(url) {
    return /^https?:\/\/[^\s/$.?#].[^\s]*$/i.test(url);
}

function downloadFile(url, progressBar) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();

        xhr.open("POST", "/download");
        xhr.setRequestHeader("Content-Type", "application/json");

        xhr.onloadstart = () => {
            progressBar.querySelector(".progress-inner").style.width = "0%";
        };

        xhr.onprogress = (e) => {
            if (e.lengthComputable) {
                const percentComplete = Math.round((e.loaded / e.total) * 100);
                progressBar.querySelector(".progress-inner").style.width = `${percentComplete}%`;
            }
        };

        xhr.onload = () => {
            if (xhr.status === 200) {
                try {
                    const response = JSON.parse(xhr.responseText);
                    if (response.success) {
                        progressBar.querySelector(".progress-inner").style.width = "100%";
                        progressBar.querySelector(".progress-inner").style.backgroundColor = "#4CAF50";
                        setTimeout(() => {
                            progressBar.remove();
                            checkAllUploadsCompleted();
                        }, 3000);
                        resolve();
                    } else {
                        progressBar.remove();
                        checkAllUploadsCompleted();
                        reject(response.error || "Unknown server error");
                    }
                } catch (error) {
                    progressBar.remove();
                    checkAllUploadsCompleted();
                    reject("Invalid JSON response");
                }
            } else {
                progressBar.remove();
                checkAllUploadsCompleted();
                reject(`HTTP ${xhr.status}`);
            }
        };

        xhr.onerror = () => {
            progressBar.remove();
            checkAllUploadsCompleted();
            reject("Network Error");
        };

        xhr.send(JSON.stringify({ url }));
    });
}

function createProgressBar(url) {
    const progressBar = document.createElement("div");
    progressBar.className = "progress-bar";

    const label = document.createElement("div");
    label.className = "progress-label";
    label.textContent = url;
    label.title = url;

    const progress = document.createElement("div");
    progress.className = "progress";

    const progressInner = document.createElement("div");
    progressInner.className = "progress-inner";

    progress.appendChild(progressInner);
    progressBar.appendChild(label);
    progressBar.appendChild(progress);

    progressContainer.appendChild(progressBar);

    return progressBar;
}

function checkAllUploadsCompleted() {
    if (progressContainer.children.length === 0) {
        progressBtn.disabled = true;
    }
}

function downloadFailedUrls(failedUrls) {
    const blob = new Blob([failedUrls.join("\n")], { type: "text/plain" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "failed_urls.txt";
    link.click();
}

const urlListInput = document.getElementById("url-list-input");
fetchBtn.disabled = true;

urlListInput.addEventListener("input", () => {
    fetchBtn.disabled = urlListInput.value.trim() === "";
});

document.getElementById('nav-upload-btn').addEventListener('click', () => {
    window.location.href = '/upload';
});

document.getElementById('nav-filemanager-btn').addEventListener('click', () => {
    window.location.href = '/files';
});

document.getElementById('nav-logout-btn').addEventListener('click', () => {
    window.location.href = '/logout';
});
document.getElementById('nav-Fetch-btn').addEventListener('click', () => {
    window.location.href = '/download';
});

const hamburgerMenu = document.getElementById("hamburger-menu");
const navButtons = document.getElementById("header-nav-buttons");

hamburgerMenu.addEventListener("click", () => {
    hamburgerMenu.classList.toggle("active");
    navButtons.classList.toggle("active");
});
fetchBtn.disabled = true;

urlListInput.addEventListener("input", () => {
    fetchBtn.disabled = urlListInput.value.trim() === "";
});


const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const uploadBtn = document.getElementById("upload-btn");
const showProgressBtn = document.getElementById("show-progress-btn");
const progressPopup = document.getElementById("progress-popup");
const closePopupBtn = document.getElementById("close-popup-btn");
const progressContainer = document.getElementById("progress-container");

let filesToUpload = [];
let isUploading = false;

dropZone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (e) => {
    filesToUpload = Array.from(e.target.files);
    updateFileNameDisplay();
    uploadBtn.disabled = filesToUpload.length === 0;
});

dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
});

dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    filesToUpload = Array.from(e.dataTransfer.files);
    updateFileNameDisplay();
    uploadBtn.disabled = filesToUpload.length === 0;
});

function updateFileNameDisplay() {
    const fileNameDisplay = document.getElementById("file-name");
    if (filesToUpload.length > 0) {
        fileNameDisplay.textContent = `${filesToUpload.length} files selected`;
    } else {
        fileNameDisplay.textContent = "";
    }
}

uploadBtn.addEventListener("click", () => {
    if (filesToUpload.length > 0 && !isUploading) {
        isUploading = true;
        uploadFiles();
        showProgressBtn.disabled = false;
    }
});

showProgressBtn.addEventListener("click", () => {
    progressPopup.style.display = "flex";
});

closePopupBtn.addEventListener("click", () => {
    progressPopup.style.display = "none";
});

async function uploadFiles() {
    for (let i = 0; i < filesToUpload.length; i += 3) {
        const batch = filesToUpload.slice(i, i + 3);
        const progressBars = batch.map(createProgressBar);

        for (let j = 0; j < batch.length; j++) {
            const file = batch[j];

            // await new Promise((delay) => setTimeout(delay, 500));
            await uploadFileWithRealProgress(file, progressBars[j]);
        }
    }
    isUploading = false;
    showProgressBtn.disabled = true;
}

function checkAllUploadsCompleted() {
    if (progressContainer.children.length === 0) {
        showProgressBtn.disabled = true;
    }
}

function uploadFileWithRealProgress(file, progressBar) {
    return new Promise((resolve) => {
        const xhr = new XMLHttpRequest();
        const formData = new FormData();

        formData.append("file", file);

        xhr.upload.addEventListener("progress", (e) => {
            if (e.lengthComputable) {
                const percentComplete = Math.round((e.loaded / e.total) * 100);
                progressBar.querySelector(".progress-inner").style.width = `${percentComplete}%`;
            }
        });

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
                        console.error("Server error:", response.error);
                    }
                } catch (error) {
                    console.error("Invalid JSON response");
                }
            } else {
                console.error("Upload failed for file:", file.name);
            }
        };

        xhr.onerror = () => {
            console.error("Error occurred during upload:", file.name);
        };

        xhr.open("POST", "/upload");
        xhr.send(formData);
    });
}


function createProgressBar(file) {
    const progressBar = document.createElement("div");
    progressBar.className = "progress-bar";

    const label = document.createElement("div");
    label.className = "progress-label";
    label.textContent = file.name;
    label.title = file.name;

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

document.getElementById('upload-file-btn').addEventListener('click', () => {
    window.location.href = '/upload';
});

document.getElementById('filemanager-btn').addEventListener('click', () => {
    window.location.href = '/files';
});
document.getElementById('logout-btn').addEventListener('click', () => {
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

document.addEventListener("DOMContentLoaded", () => {
  const fileManagerContainer = document.querySelector(".files-grid");
  const paginationContainer = document.querySelector(".pagination");

  const itemsPerRow = window.matchMedia("(hover: none) and (pointer: coarse)").matches ? 2 : 3;
  const itemsPerPage = window.matchMedia("(hover: none) and (pointer: coarse)").matches ? 4 : 6;
  let currentPage = 1;
  let allFiles = [];

  function createFileElement(file) {
    const fileElement = document.createElement("div");
    fileElement.classList.add("file-item");

    const isVideoFile = /(mp4|webm|avi|mov|mkv|flv|wmv)$/i.test(file.name);
    const isAudioFile = /(mp3|wav|m4a|ogg|flac|aac)$/i.test(file.name);
    const isImageFile = /(png|jpg|jpeg|gif|bmp|svg|webp|tiff)$/i.test(file.name);
    const isPdfFile = /\.pdf$/i.test(file.name);
    const isArchiveFile = /(zip|rar|7z|tar|gz|bz2)$/i.test(file.name);
    const isTextFile = /(txt|docx|doc|odt)$/i.test(file.name);

    if (isVideoFile) {
      const videoElement = document.createElement("video");
      videoElement.src = file.path;
      videoElement.classList.add("thumbnail");
      videoElement.muted = true;

      videoElement.addEventListener("loadeddata", () => {
        videoElement.currentTime = 1;
      });

      videoElement.addEventListener("seeked", () => {
        const canvas = document.createElement("canvas");
        canvas.width = videoElement.videoWidth;
        canvas.height = videoElement.videoHeight;

        const ctx = canvas.getContext("2d");
        ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);

        const thumbnailImage = document.createElement("img");
        thumbnailImage.src = canvas.toDataURL();
        thumbnailImage.classList.add("thumbnail");
        fileElement.replaceChild(thumbnailImage, videoElement);
      });

      videoElement.load();
      fileElement.appendChild(videoElement);
    } else if (isAudioFile) {
      const icon = document.createElement("div");
      icon.classList.add("file-icon");
      icon.textContent = "🎵";
      fileElement.appendChild(icon);
    } else if (isImageFile) {
      const imgElement = document.createElement("img");
      imgElement.src = file.path;
      imgElement.alt = file.name;
      imgElement.classList.add("thumbnail");
      fileElement.appendChild(imgElement);
    } else if (isPdfFile) {
      const icon = document.createElement("div");
      icon.classList.add("file-icon");
      icon.textContent = "📕";
      fileElement.appendChild(icon);
    } else if (isArchiveFile) {
      const icon = document.createElement("div");
      icon.classList.add("file-icon");
      icon.textContent = "📦";
      fileElement.appendChild(icon);
    } else if (isTextFile) {
      const icon = document.createElement("div");
      icon.classList.add("file-icon");
      icon.textContent = "📜";
      fileElement.appendChild(icon);
    }else {
      const icon = document.createElement("div");
      icon.classList.add("file-icon");
      icon.textContent = "📄";
      fileElement.appendChild(icon);
    }

    const fileName = document.createElement("p");
    fileName.textContent = file.name;
    fileName.classList.add("file-name");
    fileElement.appendChild(fileName);

    const downloadButton = document.createElement("a");
    downloadButton.href = file.path;
    downloadButton.download = file.name;
    downloadButton.textContent = "Download";
    downloadButton.classList.add("download-button");
    fileElement.appendChild(downloadButton);

    const ViewButton = document.createElement("button");
    ViewButton.textContent = "View File";
    ViewButton.classList.add("View-button");
    ViewButton.disabled = !(isVideoFile || isAudioFile || isImageFile);
    ViewButton.addEventListener("click", () => {
      if (isVideoFile || isAudioFile || isImageFile) {
        window.open(file.path, "_blank");
      }
    });
    fileElement.appendChild(ViewButton);

    const deleteButton = document.createElement("button");
    deleteButton.textContent = "Delete";
    deleteButton.classList.add("delete-button");
    deleteButton.addEventListener("click", () => deleteFile(file.id));
    fileElement.appendChild(deleteButton);

    return fileElement;
  }

  function renderFiles() {
    fileManagerContainer.innerHTML = "";

    const start = (currentPage - 1) * itemsPerPage;
    const end = start + itemsPerPage;
    const filesToShow = allFiles.slice(start, end);

    filesToShow.forEach((file) => {
      const fileElement = createFileElement(file);
      fileManagerContainer.appendChild(fileElement);
    });

    renderPagination();
  }

  function renderPagination() {
    paginationContainer.innerHTML = "";
    const totalPages = Math.ceil(allFiles.length / itemsPerPage);

    const prevButton = document.createElement("button");
    prevButton.textContent = "Previous";
    prevButton.disabled = currentPage === 1;
    prevButton.addEventListener("click", () => {
      currentPage--;
      renderFiles();
    });
    paginationContainer.appendChild(prevButton);

    const pageInfo = document.createElement("span");
    pageInfo.id = "page-info";
    pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
    paginationContainer.appendChild(pageInfo);

    const nextButton = document.createElement("button");
    nextButton.textContent = "Next";
    nextButton.disabled = currentPage === totalPages;
    nextButton.addEventListener("click", () => {
      currentPage++;
      renderFiles();
    });
    paginationContainer.appendChild(nextButton);
  }

  async function loadFiles() {
    try {
      const response = await fetch("/api/files");
      if (!response.ok) throw new Error("Failed to load files.");

      const data = await response.json();
      allFiles = data.files.sort((a, b) => b.id - a.id);

      renderFiles();
    } catch (error) {
      console.error(error);
      fileManagerContainer.innerHTML = "<p class='error'>Failed to load files. Please try again later.</p>";
    }
  }

  async function deleteFile(fileId) {
    const confirmDelete = confirm("Are you sure you want to delete this file?");
    if (!confirmDelete) return;

    try {
      const response = await fetch(`/api/files/${fileId}`, {
        method: "DELETE",
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || "Failed to delete file.");
      }

      allFiles = allFiles.filter((file) => file.id !== fileId);
      renderFiles();
    } catch (error) {
      console.error("Error deleting file:", error);
    }
  }

  loadFiles();

  document.getElementById("upload-file-btn").addEventListener("click", () => {
    window.location.href = "/upload";
  });

  document.getElementById("filemanager-btn").addEventListener("click", () => {
    window.location.href = "/files";
  });

  document.getElementById("logout-btn").addEventListener("click", () => {
    window.location.href = "/logout";
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
});

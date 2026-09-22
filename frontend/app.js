/**
 * ImageVision AI — Client-Side Application Logic
 *
 * Handles drag-and-drop / file selection, uploads to POST /upload,
 * and polls GET /results/{imageId} every 2 seconds until Rekognition
 * classification completes.
 */

// ============================================================================
// CONFIGURATION
// Set your API Gateway Base URL here (or leave as window.location.origin for local)
// Format: "https://<api-id>.execute-api.<region>.amazonaws.com/dev"
// ============================================================================
const API_BASE_URL = window.API_BASE_URL || window.location.origin;

const POLL_INTERVAL_MS = 2000; // Poll every 2 seconds
const MAX_POLL_ATTEMPTS = 30;   // 60 seconds timeout limit

// ============================================================================
// DOM Elements
// ============================================================================
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const uploadSection = document.getElementById("uploadSection");
const uploadError = document.getElementById("uploadError");
const uploadErrorMessage = document.getElementById("uploadErrorMessage");

const loadingSection = document.getElementById("loadingSection");
const loadingTitle = document.getElementById("loadingTitle");
const loadingSubtitle = document.getElementById("loadingSubtitle");
const pollStatusText = document.getElementById("pollStatusText");

const resultsSection = document.getElementById("resultsSection");
const resultThumbnail = document.getElementById("resultThumbnail");
const predictionLabel = document.getElementById("predictionLabel");
const confidenceValue = document.getElementById("confidenceValue");
const meterFill = document.getElementById("meterFill");
const confidenceGrade = document.getElementById("confidenceGrade");
const metaImageId = document.getElementById("metaImageId");
const metaS3Key = document.getElementById("metaS3Key");
const resultTimestamp = document.getElementById("resultTimestamp");
const resetBtn = document.getElementById("resetBtn");

let currentPollTimer = null;
let pollAttempts = 0;
let currentPreviewDataUrl = "";

// ============================================================================
// Event Listeners — Drag and Drop & File Input
// ============================================================================
dropzone.addEventListener("click", () => fileInput.click());

dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fileInput.click();
  }
});

["dragenter", "dragover"].forEach((eventName) => {
  dropzone.addEventListener(eventName, (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropzone.classList.add("drag-over");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  dropzone.addEventListener(eventName, (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropzone.classList.remove("drag-over");
  });
});

dropzone.addEventListener("drop", (e) => {
  const dt = e.dataTransfer;
  const files = dt.files;
  if (files && files.length > 0) {
    handleFile(files[0]);
  }
});

fileInput.addEventListener("change", (e) => {
  if (e.target.files && e.target.files.length > 0) {
    handleFile(e.target.files[0]);
  }
});

resetBtn.addEventListener("click", resetToUpload);

// ============================================================================
// File Handling & Upload
// ============================================================================
function showError(message) {
  uploadErrorMessage.textContent = message;
  uploadError.classList.remove("hidden");
}

function hideError() {
  uploadError.classList.add("hidden");
}

function handleFile(file) {
  hideError();

  const validTypes = ["image/jpeg", "image/png", "image/jpg"];
  if (!validTypes.includes(file.type)) {
    showError("Unsupported file type. Please upload a JPEG or PNG image.");
    return;
  }

  const maxSize = 5 * 1024 * 1024; // 5 MB
  if (file.size > maxSize) {
    showError("Image file is too large. Maximum size is 5MB.");
    return;
  }

  const reader = new FileReader();
  reader.onload = function (event) {
    currentPreviewDataUrl = event.target.result;
    startUpload(currentPreviewDataUrl);
  };
  reader.onerror = function () {
    showError("Failed to read image file. Please try again.");
  };
  reader.readAsDataURL(file);
}

// ============================================================================
// API Communication
// ============================================================================
async function startUpload(base64DataUrl) {
  // Switch to loading state
  uploadSection.classList.add("hidden");
  loadingSection.classList.remove("hidden");
  resultsSection.classList.add("hidden");

  loadingTitle.textContent = "Uploading image...";
  loadingSubtitle.textContent = "Transferring image to Amazon S3 storage";
  pollStatusText.textContent = "Connecting to API Gateway...";

  try {
    const uploadUrl = `${API_BASE_URL.replace(/\/+$/, "")}/upload`;
    const response = await fetch(uploadUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: jsonSafePayload(base64DataUrl),
    });

    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.error || `Upload failed with status ${response.status}`);
    }

    const data = await response.json();
    const imageId = data.imageId;

    if (!imageId) {
      throw new Error("No imageId returned from upload endpoint.");
    }

    // Begin polling for results
    loadingTitle.textContent = "Classifying image...";
    loadingSubtitle.textContent = "Amazon Rekognition is detecting objects and labels";
    pollAttempts = 0;
    pollResults(imageId);

  } catch (err) {
    console.error("Upload error:", err);
    resetToUpload();
    showError(err.message || "Failed to upload image. Please check API connection.");
  }
}

function jsonSafePayload(dataUrl) {
  return JSON.stringify({
    image: dataUrl,
  });
}

// ============================================================================
// Polling for Results
// ============================================================================
async function pollResults(imageId) {
  pollAttempts++;
  pollStatusText.textContent = `Analyzing image... (Checking result: ${pollAttempts})`;

  try {
    const resultsUrl = `${API_BASE_URL.replace(/\/+$/, "")}/results/${encodeURIComponent(imageId)}`;
    const response = await fetch(resultsUrl, {
      method: "GET",
      headers: {
        "Accept": "application/json",
      },
    });

    if (response.status === 200) {
      // Classification ready!
      const resultData = await response.json();
      displayResults(imageId, resultData);
      return;
    }

    if (response.status === 202) {
      // Still processing
      if (pollAttempts >= MAX_POLL_ATTEMPTS) {
        throw new Error("Classification timed out. Please try again.");
      }

      currentPollTimer = setTimeout(() => {
        pollResults(imageId);
      }, POLL_INTERVAL_MS);
      return;
    }

    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.error || `Classification query returned status ${response.status}`);

  } catch (err) {
    console.error("Polling error:", err);
    resetToUpload();
    showError(err.message || "Failed to retrieve classification results.");
  }
}

// ============================================================================
// Display Results
// ============================================================================
function displayResults(imageId, result) {
  loadingSection.classList.add("hidden");
  resultsSection.classList.remove("hidden");

  // Thumbnail
  resultThumbnail.src = currentPreviewDataUrl;

  // Label & Confidence
  const label = result.label || "Unknown";
  const confidence = typeof result.confidence === "number" ? result.confidence : parseFloat(result.confidence || 0);
  const formattedConfidence = confidence.toFixed(1);

  predictionLabel.textContent = label;
  confidenceValue.textContent = `${formattedConfidence}%`;

  // Animate Meter Fill
  setTimeout(() => {
    meterFill.style.width = `${Math.min(confidence, 100)}%`;
  }, 50);

  // Confidence grade badge
  if (confidence >= 90) {
    confidenceGrade.textContent = "High Confidence";
    confidenceGrade.className = "meta-pill grade-high";
  } else if (confidence >= 70) {
    confidenceGrade.textContent = "Medium Confidence";
    confidenceGrade.className = "meta-pill grade-medium";
  } else {
    confidenceGrade.textContent = "Low Confidence";
    confidenceGrade.className = "meta-pill";
  }

  // Metadata
  metaImageId.textContent = `ID: ${imageId}`;
  metaS3Key.textContent = result.s3Key || imageId;

  if (result.timestamp) {
    try {
      const date = new Date(result.timestamp);
      resultTimestamp.textContent = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      resultTimestamp.textContent = "Just now";
    }
  } else {
    resultTimestamp.textContent = "Just now";
  }
}

// ============================================================================
// Reset UI
// ============================================================================
function resetToUpload() {
  if (currentPollTimer) {
    clearTimeout(currentPollTimer);
    currentPollTimer = null;
  }
  fileInput.value = "";
  currentPreviewDataUrl = "";
  meterFill.style.width = "0%";

  uploadSection.classList.remove("hidden");
  loadingSection.classList.add("hidden");
  resultsSection.classList.add("hidden");
  hideError();
}

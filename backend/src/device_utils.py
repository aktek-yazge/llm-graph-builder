"""
Apple Silicon / Metal Performance Shaders (MPS) Device Detection Utility
Bu modül Apple Silicon Mac'lerde Metal GPU acceleration'ı etkinleştirir
"""

# torch is only needed for ML processing, which is done in celery_worker
try:
    import torch
except (ImportError, ModuleNotFoundError):
    torch = None  # ML processing is in celery_worker
import logging
import os
# psutil is optional - only needed for system monitoring
try:
    import psutil
except (ImportError, ModuleNotFoundError):
    psutil = None  # System monitoring is optional
import platform

logger = logging.getLogger(__name__)

def get_optimal_device():
    """
    En uygun PyTorch device'ını döndürür
    Apple Silicon için MPS, CUDA varsa CUDA, yoksa CPU
    """
    
    # Environment variable kontrolü
    force_cpu = os.getenv('FORCE_CPU', 'false').lower() == 'true'
    if force_cpu:
        logger.info("🔧 FORCE_CPU=true, CPU kullanılıyor")
        return torch.device('cpu')
    
    # Apple Silicon MPS kontrolü
    if torch.backends.mps.is_available() and torch.backends.mps.is_built():
        logger.info("🍎 Apple Silicon MPS (Metal) GPU kullanılıyor")
        return torch.device('mps')
    
    # CUDA kontrolü
    elif torch.cuda.is_available():
        device = torch.device('cuda')
        logger.info(f"🚀 CUDA GPU kullanılıyor: {torch.cuda.get_device_name()}")
        return device
    
    # Fallback to CPU
    else:
        logger.info("💻 CPU kullanılıyor (GPU acceleration yok)")
        return torch.device('cpu')

def optimize_for_apple_silicon():
    """
    Apple Silicon için PyTorch optimizasyonları
    """
    if torch is None:
        logger.warning("⚠️ PyTorch not available (only in celery_worker), skipping optimizations")
        return
    
    if torch.backends.mps.is_available():
        # MPS optimizasyonları
        logger.info("🍎 Apple Silicon MPS optimizasyonları etkinleştiriliyor...")
        
        # Environment variables
        os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'
        os.environ['ACCELERATE_USE_MPS'] = '1'
        
        return True
    return False

def print_device_info():
    """
    Sistem ve PyTorch device bilgilerini yazdır
    """
    print("=" * 50)
    print("🔧 PyTorch Device Information")
    print("=" * 50)
    
    if torch is None:
        print("⚠️ PyTorch not available (only in celery_worker)")
        return
    
    print(f"PyTorch Version: {torch.__version__}")
    
    # Platform info
    import sys
    print(f"Python Version: {sys.version}")
    print(f"Platform: {sys.platform}")
    
    # System information
    print(f"System: {platform.system()} {platform.release()}")
    print(f"Machine: {platform.machine()}")
    print(f"Processor: {platform.processor()}")
    
    # CPU Information
    if psutil is not None:
        print(f"CPU Cores (Physical): {psutil.cpu_count(logical=False)}")
        print(f"CPU Cores (Logical): {psutil.cpu_count(logical=True)}")
        
        # Memory Information
        memory = psutil.virtual_memory()
        print(f"Total Memory: {memory.total / (1024**3):.2f} GB")
        print(f"Available Memory: {memory.available / (1024**3):.2f} GB")
        print(f"Memory Usage: {memory.percent:.1f}%")
    else:
        print("⚠️ psutil not available for system info")
    
    # MPS (Metal) support
    if hasattr(torch.backends, 'mps'):
        print(f"MPS Available: {torch.backends.mps.is_available()}")
        print(f"MPS Built: {torch.backends.mps.is_built()}")
    
    # CUDA support  
    print(f"CUDA Available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"CUDA Device Count: {torch.cuda.device_count()}")
        print(f"CUDA Device Name: {torch.cuda.get_device_name()}")
    
    # Selected device
    device = get_optimal_device()
    print(f"Selected Device: {device}")
    print("=" * 50)

# Modül import edildiğinde otomatik optimizasyon
if __name__ != "__main__":
    optimize_for_apple_silicon()
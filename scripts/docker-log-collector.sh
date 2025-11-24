#!/bin/bash
# Docker Log Collector Script
# Collects Docker container logs and saves them to host with container ID-based folders
# Handles log rotation when files exceed 500MB

LOG_BASE_DIR="${DOCKER_LOG_DIR:-~/docker-logs}"
MAX_FILE_SIZE=524288000  # 500MB in bytes
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Function to get container ID from container name
get_container_id() {
    local container_name=$1
    docker ps -aqf "name=^${container_name}$" | head -n 1
}

# Function to collect logs for a container
collect_logs() {
    local container_name=$1
    local container_id=$(get_container_id "$container_name")
    
    if [ -z "$container_id" ]; then
        echo "⚠️  Container not found: $container_name"
        return 1
    fi
    
    # Create directory structure: ~/docker-logs/<container-name>/<container-id>/
    local log_dir="${LOG_BASE_DIR}/${container_name}/${container_id}"
    mkdir -p "$log_dir"
    
    # Get Docker log file path
    local docker_log_path="/var/lib/docker/containers/${container_id}/${container_id}-json.log"
    
    # Check if log file exists and get its size
    if [ -f "$docker_log_path" ]; then
        local file_size=$(stat -f%z "$docker_log_path" 2>/dev/null || stat -c%s "$docker_log_path" 2>/dev/null)
        
        # If file exceeds max size, rotate
        if [ "$file_size" -gt "$MAX_FILE_SIZE" ]; then
            local rotated_file="${log_dir}/container-${container_id}-${TIMESTAMP}.log"
            echo "📦 Rotating log file (size: $file_size bytes) -> $rotated_file"
            cp "$docker_log_path" "$rotated_file"
            # Truncate original log (Docker will continue writing)
            > "$docker_log_path"
        else
            # Copy current log
            local current_log="${log_dir}/container-${container_id}-current.log"
            cp "$docker_log_path" "$current_log"
        fi
    else
        # Use docker logs command as fallback
        local current_log="${log_dir}/container-${container_id}-current.log"
        docker logs "$container_id" > "$current_log" 2>&1
    fi
    
    echo "✅ Collected logs for $container_name ($container_id) -> $log_dir"
}

# Main execution
if [ $# -eq 0 ]; then
    echo "Usage: $0 <container-name1> [container-name2] ..."
    echo "Or set DOCKER_LOG_DIR environment variable to change log directory (default: ~/docker-logs)"
    exit 1
fi

# Expand ~ to home directory
LOG_BASE_DIR="${LOG_BASE_DIR/#\~/$HOME}"

echo "📋 Docker Log Collector"
echo "   Log directory: $LOG_BASE_DIR"
echo "   Max file size: 500MB"
echo ""

# Collect logs for each container
for container_name in "$@"; do
    collect_logs "$container_name"
done

echo ""
echo "✅ Log collection completed!"



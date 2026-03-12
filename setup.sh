#!/usr/bin/env bash
#
# Nexus-MCP Setup Script
# Sets up Python virtual environment and installs all dependencies
#
# Usage:
#   ./setup.sh              # Default: create venv + install with dev deps
#   ./setup.sh --clean      # Remove existing venv before creating new
#   ./setup.sh --prod       # Install production dependencies only (no dev)
#   ./setup.sh --reranker   # Include optional FlashRank reranker
#   ./setup.sh --no-verify  # Skip verification step
#   ./setup.sh --help       # Show this help message
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
VENV_DIR=".venv"
MIN_PYTHON_VERSION="3.10"

# Parse arguments
CLEAN=false
PROD_ONLY=false
SKIP_VERIFY=false
RERANKER=false

print_usage() {
    echo -e "${CYAN}Nexus-MCP Setup Script${NC}"
    echo ""
    echo "Usage: ./setup.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --clean      Remove existing virtual environment before creating new"
    echo "  --prod       Install production dependencies only (skip dev deps)"
    echo "  --reranker   Include optional FlashRank reranker"
    echo "  --no-verify  Skip verification step"
    echo "  --help       Show this help message"
    echo ""
    echo "Examples:"
    echo "  ./setup.sh                       # Dev install (pytest, ruff, mypy)"
    echo "  ./setup.sh --clean               # Remove old venv, create new one"
    echo "  ./setup.sh --prod                # Production-only install"
    echo "  ./setup.sh --reranker            # Dev install + FlashRank reranker"
    echo "  ./setup.sh --clean --prod        # Clean install, production only"
}

for arg in "$@"; do
    case $arg in
        --clean)
            CLEAN=true
            shift
            ;;
        --prod)
            PROD_ONLY=true
            shift
            ;;
        --reranker)
            RERANKER=true
            shift
            ;;
        --no-verify)
            SKIP_VERIFY=true
            shift
            ;;
        --help|-h)
            print_usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $arg${NC}"
            print_usage
            exit 1
            ;;
    esac
done

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║             Nexus-MCP — Environment Setup                ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Step 1: Check Python version
echo -e "${BLUE}[1/6]${NC} Checking Python version..."

if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo -e "${RED}✗ Python not found. Please install Python ${MIN_PYTHON_VERSION} or higher.${NC}"
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.minor)")

if [[ $PYTHON_MAJOR -lt 3 ]] || [[ $PYTHON_MAJOR -eq 3 && $PYTHON_MINOR -lt 10 ]]; then
    echo -e "${RED}✗ Python ${MIN_PYTHON_VERSION}+ required. Found: ${PYTHON_VERSION}${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Python ${PYTHON_VERSION} detected (${PYTHON_CMD})${NC}"

# Step 2: Handle existing venv
echo -e "${BLUE}[2/6]${NC} Setting up virtual environment..."

if [[ -d "$VENV_DIR" ]]; then
    if [[ "$CLEAN" = true ]]; then
        echo -e "${YELLOW}  Removing existing virtual environment...${NC}"
        rm -rf "$VENV_DIR"
        echo -e "${GREEN}  ✓ Old venv removed${NC}"
    else
        echo -e "${YELLOW}  ⚠ Virtual environment already exists. Use --clean to recreate.${NC}"
    fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
    echo -e "  Creating virtual environment in ${VENV_DIR}..."
    $PYTHON_CMD -m venv "$VENV_DIR"
    echo -e "${GREEN}  ✓ Virtual environment created${NC}"
else
    echo -e "${GREEN}  ✓ Using existing virtual environment${NC}"
fi

# Step 3: Activate venv and upgrade pip
echo -e "${BLUE}[3/6]${NC} Activating environment and upgrading pip..."

source "$VENV_DIR/bin/activate"

pip install --upgrade pip --quiet
echo -e "${GREEN}✓ pip upgraded to $(pip --version | awk '{print $2}')${NC}"

# Step 4: Install dependencies
echo -e "${BLUE}[4/6]${NC} Installing dependencies..."

if [[ "$PROD_ONLY" = true ]]; then
    if [[ "$RERANKER" = true ]]; then
        echo -e "  Installing production + reranker dependencies..."
        pip install -e ".[reranker]" --quiet
    else
        echo -e "  Installing production dependencies..."
        pip install -e . --quiet
    fi
    echo -e "${GREEN}✓ Production dependencies installed${NC}"
else
    if [[ "$RERANKER" = true ]]; then
        echo -e "  Installing all dependencies (dev + reranker)..."
        pip install -e ".[dev,reranker]" --quiet
    else
        echo -e "  Installing all dependencies (including dev)..."
        pip install -e ".[dev]" --quiet
    fi
    echo -e "${GREEN}✓ All dependencies installed${NC}"
fi

# Step 5: Verify installation
if [[ "$SKIP_VERIFY" = true ]]; then
    echo -e "${BLUE}[5/6]${NC} ${YELLOW}Skipping verification (--no-verify)${NC}"
else
    echo -e "${BLUE}[5/6]${NC} Verifying installation..."

    if command -v nexus-mcp &> /dev/null; then
        echo -e "${GREEN}  ✓ nexus-mcp CLI available${NC}"
    else
        echo -e "${RED}  ✗ nexus-mcp CLI not found${NC}"
        exit 1
    fi

    if $PYTHON_CMD -c "import nexus_mcp; print('OK')" &> /dev/null; then
        echo -e "${GREEN}  ✓ Core imports verified${NC}"
    else
        echo -e "${RED}  ✗ Import verification failed${NC}"
        exit 1
    fi
fi

# Step 6: Print integration instructions
echo -e "${BLUE}[6/6]${NC} Setup complete!"

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Setup Complete! ✓                      ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "To activate the environment:"
echo -e "  ${CYAN}source ${VENV_DIR}/bin/activate${NC}"
echo ""
echo -e "To start the server:"
echo -e "  ${CYAN}nexus-mcp${NC}"
echo ""
echo -e "To run the self-test demo:"
echo -e "  ${CYAN}python self_test/demo_mcp.py${NC}"
echo ""
echo -e "To add to Claude Code:"
echo -e "  ${CYAN}claude mcp add nexus-mcp -- nexus-mcp${NC}"
echo ""
if [[ "$PROD_ONLY" = false ]]; then
    echo -e "To run tests:"
    echo -e "  ${CYAN}pytest -v${NC}"
    echo ""
fi

document.addEventListener('DOMContentLoaded', () => {
    // Referencias a elementos
    const searchInput = document.getElementById('main-search-input');
    const micButton = document.querySelector('.mic-button');
    const projectsGrid = document.getElementById('projects-grid');

    // Fetch existing projects/ideas and configuration from the local backend
    fetch('/api/data')
        .then(response => response.json())
        .then(data => {
            const maxBubbles = data.maxBubbles || 12; // Configurable limit
            const items = data.items || [];

            // Limit items to maxBubbles defined in config
            const itemsToDisplay = items.slice(0, maxBubbles);

            // Render existings items only
            itemsToDisplay.forEach(item => {
                const gridItem = document.createElement('div');
                gridItem.classList.add('grid-item');

                const itemText = document.createElement('span');
                itemText.classList.add('item-text');
                itemText.textContent = item.name || 'TEXTO';

                gridItem.appendChild(itemText);
                projectsGrid.appendChild(gridItem);

                // Add interactive click effect logic wrapper
                gridItem.addEventListener('click', function () {
                    // Simple pulse animation on click
                    this.style.transform = 'scale(0.95)';
                    setTimeout(() => {
                        this.style.transform = ''; // reset to hover state or default
                    }, 150);
                });
            });
        })
        .catch(error => console.error("Error cargando configuración o items:", error));

    // AQUI IRA LA FUNCIONALIDAD DE BACKEND PARA BUSCAR O CREAR IDEAS Y PROYECTOS
});

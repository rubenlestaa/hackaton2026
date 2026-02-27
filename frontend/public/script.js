document.addEventListener('DOMContentLoaded', () => {
    const homeView = document.getElementById('home-view');
    const projectsView = document.getElementById('projects-view');
    const searchInput = document.getElementById('main-search-input');

    // Handle click on home view to transition to projects
    homeView.addEventListener('click', () => {
        // Hide the home view
        homeView.classList.remove('active-view');
        homeView.classList.add('hidden-view');

        // Show the projects view with a stagger
        setTimeout(() => {
            projectsView.classList.remove('hidden-view');
            projectsView.classList.add('active-view');

            // Optionally focus the search/create input when arriving
            setTimeout(() => {
                searchInput.focus();
            }, 600);
        }, 50);

        // Remove event listener as we don't go back
        homeView.style.pointerEvents = 'none';
    });

    // Add interactive click effect to grid items
    const gridItems = document.querySelectorAll('.grid-item');
    gridItems.forEach(item => {
        item.addEventListener('click', function () {
            // Simple pulse animation on click
            this.style.transform = 'scale(0.95)';
            setTimeout(() => {
                this.style.transform = ''; // reset to hover state or default
            }, 150);
        });
    });
});

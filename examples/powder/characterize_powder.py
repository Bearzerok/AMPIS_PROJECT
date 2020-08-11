import numpy as np
import pathlib
import pycocotools.mask as RLE
import skimage
import skimage.io
import sys

from detectron2.structures import Instances

ampis_root = pathlib.Path('../../src/')
sys.path.append(str(ampis_root))
from ampis import analyze, visualize
from ampis.structures import boxes_to_array, masks_to_rle, InstanceSet


def _rle_satellite_match(particles, satellites, match_thresh=0.5):
    """
    TODO docs
    Args:
        particles:
        satellites:
        match_thresh:

    Returns:

    """

    particles = masks_to_rle(particles)
    satellites = masks_to_rle(satellites)

    satellite_matches = []
    intersection_scores = []

    particles_matched_bool = np.zeros(len(particles), dtype=np.bool)
    satellites_unmatched = []

    for satellite_idx, satellite_mask in enumerate(satellites):

        intersects = RLE.area([RLE.merge([satellite_mask, pmask], intersect=True) for pmask in particles]) \
            / RLE.area(satellite_mask)

        iscore_amax = np.argmax(intersects)
        iscore_max = intersects[iscore_amax]

        if iscore_max > match_thresh:
            satellite_matches.append([satellite_idx, iscore_amax])
            particles_matched_bool[iscore_amax] = True
            intersection_scores.append(iscore_max)

        else:
            satellites_unmatched.append(satellite_idx)

    particles_unmatched = np.array([i for i, matched in enumerate(particles_matched_bool) if not matched], np.int)
    satellite_matches = np.asarray(satellite_matches, np.int)
    satellites_unmatched = np.asarray(satellites_unmatched, np.int)
    intersection_scores = np.asarray(intersection_scores)

    match_pairs = {x: [] for x in np.unique(satellite_matches[:, 1])}
    for match in satellite_matches:
        match_pairs[match[1]].append(match[0])


    results = {'satellite_matches': satellite_matches,
               'satellites_unmatched': satellites_unmatched,
               'particles_unmatched': particles_unmatched,
               'intersection_scores': intersection_scores,
               'match_pairs': match_pairs}

    return results


class PowderSatelliteImage(object):
    """
    Powder and satellite instance predictions for a single image
    """
    
    def __init__(self, particles=None, satellites=None, matches=None):
        self.particles = particles  # instance set of particles
        self.satellites = satellites  # instance set of satellites
        self.matches = matches  # maps satellites to their corresponding particles, see output of fast_satellite_match()
    
    def compute_matches(self, thresh=0.5):
        """
        wrapper for rle_satellite_match
        """
        self.matches = _rle_satellite_match(self.particles.instances.masks.rle,
                                            self.satellites.instances.masks.rle, thresh)
        
    def visualize_particle_with_satellites(self, p_idx, ax=None):
        '''
        visualize single particle with its associated satellites
        inputs:
            :param p_idx: - index of particle mask to be plotted. Should be a key in
                                    self.matches['particle_satellite_match_idx']
            :param ax: - matplotlib axis object to visualize results on. If None, new axis
            will be created and shown by mrcnn_visualize.display_instances() function.
        '''

        particle_mask = self.particles.instances.masks[[p_idx]]  # maintain shape
        particle_mask = masks_to_rle(particle_mask)

        particle_box = self.particles.instances.boxes[[p_idx]]
        particle_box = boxes_to_array(particle_box)

        particle_class_idx = np.zeros([1], np.int)

        s_idx = self.matches['match_pairs'][p_idx]
        satellite_masks = self.satellites.instances.masks[s_idx]
        satellite_masks = masks_to_rle(satellite_masks)

        satellite_box = self.satellites.instances.boxes[s_idx]
        satellite_box = boxes_to_array(satellite_box)

        satellite_class_idx = np.ones(len(satellite_box), np.int)

        masks = particle_mask + satellite_masks
        boxes = np.concatenate((particle_box, satellite_box), axis=0).astype(np.int)
        labels = np.concatenate((particle_class_idx, satellite_class_idx), axis=0)

        label_map = {'thing_classes': ['particle', 'satellite']}

        minbox = boxes[:, :2].min(axis=0)
        maxbox = boxes[:, 2:].max(axis=0)

        total_box = np.concatenate((minbox, maxbox), axis=0)
        c1, r1, c2, r2 = total_box

        img = skimage.io.imread(self.particles.filepath)
        img = skimage.color.gray2rgb(img)
        img = img[r1:r2, c1:c2]

        # need to trim masks to correct size
        masks = RLE.decode(masks)
        masks = masks[r1:r2, c1:c2, :]
        masks = RLE.encode(np.asfortranarray(masks))
        
        boxes[:, [0, 2]] -= c1
        boxes[:, [1, 3]] -= r1

        image_size = (r2-r1, c2-c1)
        instances = Instances(image_size, **{'masks': masks, 'boxes': boxes, 'class_idx': labels})
        iset = InstanceSet(instances=instances)
        iset.instances.colors = visualize.random_colors(len(iset.instances), iset.randomstate)

        visualize.quick_visualize_iset(img, label_map, iset, ax=ax)

    def compute_satellite_metrics(self):
        """
        Computes the number of satellites and number of particles containing at least one satellite in the image.

        The number of particles and number of particles with at least one satellite
        can be determined from the length of each list. The results are returned as
        arrays of mask areas so that size filtering can be applied as necessary before
        computing the final results..

        Parameters
        ------------

        Returns
        ------------
        mask_areas_all, mask_areas_matched: ndarray
             element array where each element is the area of each particle mask.
             mask_areas_all includes all particle masks in the image, and mask_areas_matched
             only includes the subset where the particle matched at least one satellite.
        results: dict
            dictionary with the following format:
            {
            n_satellites: int- total number of satellites in image
            n_particles_matched: int- total number of matched particles in image
            n_particles_all: int- total number of particles in image
            mask_areas_matched: ndarray- n_particles_matched element array of mask areas of each matched particle
            mask_areas_all: ndarray- n_particles_all element array of mask areas of all particles
            }
        """
        # psi must have these defined to do match
        assert None not in (self.particles, self.satellites, self.matches)

        # total number of satellites in image
        n_satellites = len(self.satellites.instances)

        # number of particles with at least one satellite
        matched_particle_idx = list(self.matches['match_pairs'])
        n_particles_matched = len(matched_particle_idx)

        # total number of particles in image
        n_particles_all = len(self.particles.instances)

        # areas of masks retrieved so size thresholding can be applied
        particle_masks_all = masks_to_rle(self.particles.instances.masks.rle)
        mask_areas_all = RLE.area(particle_masks_all)
        mask_areas_matched = mask_areas_all[matched_particle_idx]

        results = {'n_satellites': n_satellites,
                   'n_particles_matched': n_particles_matched,
                   'n_particles_all': n_particles_all,
                   'mask_areas_matched': mask_areas_matched,
                   'mask_areas_all': mask_areas_all}

        return results




